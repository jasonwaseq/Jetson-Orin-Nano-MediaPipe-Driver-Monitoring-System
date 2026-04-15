import math
import os
import threading
import time
from dataclasses import dataclass

from modules.module_bno055 import BNO055, BNO055_DEFAULT_ADDRESS, BNO055Error
from modules.module_env_init import env_bool, env_int


def _env_float(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int_auto(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value, 0)
    except ValueError:
        return default


@dataclass(frozen=True)
class IMUSpeedMonitorConfig:
    enabled: bool = False
    bus: int = 7
    address: int = BNO055_DEFAULT_ADDRESS
    poll_hz: int = 20
    threshold_mps2: float = 2.5
    sustain_seconds: float = 0.5
    cooldown_seconds: float = 8.0
    clear_ratio: float = 0.75
    axis: str = "magnitude"
    use_linear_acceleration: bool = True
    use_external_crystal: bool = True
    smoothing_alpha: float = 0.2
    debug: bool = False

    @classmethod
    def from_env(cls):
        axis = (os.getenv("MP_IMU_AXIS", "magnitude") or "magnitude").strip().lower()
        if axis not in {"magnitude", "x", "y", "z"}:
            axis = "magnitude"
        return cls(
            enabled=env_bool("MP_IMU_ENABLED", False),
            bus=env_int("MP_IMU_I2C_BUS", 7),
            address=_env_int_auto("MP_IMU_ADDRESS", BNO055_DEFAULT_ADDRESS),
            poll_hz=max(1, env_int("MP_IMU_POLL_HZ", 20)),
            threshold_mps2=max(0.1, _env_float("MP_IMU_SPEED_THRESHOLD_MPS2", 2.5)),
            sustain_seconds=max(0.0, _env_float("MP_IMU_SUSTAIN_SECONDS", 0.5)),
            cooldown_seconds=max(0.0, _env_float("MP_IMU_ALERT_COOLDOWN_SECONDS", 8.0)),
            clear_ratio=min(0.95, max(0.1, _env_float("MP_IMU_CLEAR_RATIO", 0.75))),
            axis=axis,
            use_linear_acceleration=env_bool("MP_IMU_USE_LINEAR_ACCELERATION", True),
            use_external_crystal=env_bool("MP_IMU_USE_EXTERNAL_CRYSTAL", True),
            smoothing_alpha=min(1.0, max(0.01, _env_float("MP_IMU_SMOOTHING_ALPHA", 0.2))),
            debug=env_bool("MP_IMU_DEBUG", False),
        )


class IMUSpeedMonitor:
    """Background BNO055 reader that emits MQTT/BLE/WebSocket alerts."""

    def __init__(self, router, config):
        self.router = router
        self.config = config
        self.sensor = None
        self.thread = None
        self.stop_event = threading.Event()
        self.event_count = 0

        self._smoothed_metric = None
        self._threshold_started_at = None
        self._last_alert_at = 0.0
        self._alert_active = False
        self._last_error_log_at = 0.0

    def start(self):
        self.sensor = BNO055(
            bus=self.config.bus,
            address=self.config.address,
            use_external_crystal=self.config.use_external_crystal,
        )
        self.sensor.initialize()

        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.router.emit_log(
            "IMU speed monitor started "
            f"bus=/dev/i2c-{self.config.bus} address=0x{self.config.address:02X} "
            f"axis={self.config.axis} threshold={self.config.threshold_mps2:.2f}m/s^2 "
            f"source={'linear' if self.config.use_linear_acceleration else 'raw'}"
        )
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        if self.sensor is not None:
            self.sensor.close()
            self.sensor = None

    def _run(self):
        sample_period = 1.0 / float(self.config.poll_hz)
        next_tick = time.monotonic()

        while not self.stop_event.is_set():
            now = time.monotonic()
            if now < next_tick:
                self.stop_event.wait(next_tick - now)
                continue
            next_tick = now + sample_period

            try:
                sample = self.sensor.read_sample()
                self._process_sample(sample)
            except BNO055Error as exc:
                self._log_error(exc)
                self.stop_event.wait(1.0)
            except Exception as exc:
                self._log_error(exc)
                self.stop_event.wait(1.0)

    def _process_sample(self, sample):
        vector = sample.linear_acceleration if self.config.use_linear_acceleration else sample.acceleration
        metric = self._vector_metric(vector)
        self._smoothed_metric = self._smooth(metric, self._smoothed_metric)

        if self.config.debug:
            self.router.emit_log(
                "IMU sample "
                f"metric={metric:.3f} smoothed={self._smoothed_metric:.3f} "
                f"linear={sample.linear_acceleration} accel={sample.acceleration} "
                f"calib={sample.calibration}"
            )

        now = sample.timestamp_monotonic
        clear_threshold = self.config.threshold_mps2 * self.config.clear_ratio
        above_threshold = self._smoothed_metric >= self.config.threshold_mps2
        below_clear_threshold = self._smoothed_metric < clear_threshold

        if above_threshold:
            if self._threshold_started_at is None:
                self._threshold_started_at = now
            sustained_for = now - self._threshold_started_at
            cooldown_elapsed = now - self._last_alert_at

            if (
                not self._alert_active
                and sustained_for >= self.config.sustain_seconds
                and cooldown_elapsed >= self.config.cooldown_seconds
            ):
                self._emit_speeding_alert(sample, metric, self._smoothed_metric, sustained_for)
                self._last_alert_at = now
                self._alert_active = True
        elif below_clear_threshold:
            self._threshold_started_at = None
            self._alert_active = False

    def _emit_speeding_alert(self, sample, metric, smoothed_metric, sustained_for):
        self.event_count += 1
        message = (
            f"SPEEDING / HARSH ACCELERATION DETECTED! Event #{self.event_count} "
            f"({smoothed_metric:.2f} m/s^2 on {self.config.axis})"
        )
        self.router.emit_log(message, level="warning")
        self.router.emit_alert(
            "speeding_detected",
            message,
            severity="high",
            event_count=self.event_count,
            imu_axis=self.config.axis,
            imu_metric_mps2=round(metric, 3),
            imu_smoothed_metric_mps2=round(smoothed_metric, 3),
            imu_sustained_seconds=round(sustained_for, 3),
            imu_threshold_mps2=round(self.config.threshold_mps2, 3),
            imu_vector_source="linear_acceleration" if self.config.use_linear_acceleration else "acceleration",
            imu_bus=self.config.bus,
            imu_address=f"0x{self.config.address:02X}",
            linear_acceleration_mps2={
                "x": sample.linear_acceleration[0],
                "y": sample.linear_acceleration[1],
                "z": sample.linear_acceleration[2],
            },
            acceleration_mps2={
                "x": sample.acceleration[0],
                "y": sample.acceleration[1],
                "z": sample.acceleration[2],
            },
            calibration=sample.calibration,
        )

    def _vector_metric(self, vector):
        x, y, z = vector
        if self.config.axis == "x":
            return abs(x)
        if self.config.axis == "y":
            return abs(y)
        if self.config.axis == "z":
            return abs(z)
        return math.sqrt((x * x) + (y * y) + (z * z))

    def _smooth(self, value, current):
        if current is None:
            return value
        alpha = self.config.smoothing_alpha
        return (alpha * value) + ((1.0 - alpha) * current)

    def _log_error(self, exc):
        now = time.monotonic()
        if now - self._last_error_log_at < 5.0:
            return
        self._last_error_log_at = now
        self.router.emit_log(f"IMU monitor error: {exc}", level="error")
