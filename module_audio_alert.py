import os
import queue
import sys
import threading
import time
from dataclasses import dataclass

from module_env_init import env_bool, env_int


def _import_gpio():
    try:
        import Jetson.GPIO as gpio  # pylint: disable=import-outside-toplevel
        return gpio
    except ModuleNotFoundError:
        dist_packages = "/usr/lib/python3/dist-packages"
        if dist_packages not in sys.path and os.path.isdir(dist_packages):
            sys.path.append(dist_packages)
        import Jetson.GPIO as gpio  # pylint: disable=import-outside-toplevel
        return gpio


def _import_gpio_pin_data():
    try:
        from Jetson.GPIO import gpio_pin_data  # pylint: disable=import-outside-toplevel
        return gpio_pin_data
    except ModuleNotFoundError:
        dist_packages = "/usr/lib/python3/dist-packages"
        if dist_packages not in sys.path and os.path.isdir(dist_packages):
            sys.path.append(dist_packages)
        from Jetson.GPIO import gpio_pin_data  # pylint: disable=import-outside-toplevel
        return gpio_pin_data


class _SysfsPWM:
    def __init__(self, chip_dir, pwm_id, frequency_hz):
        self._chip_dir = chip_dir
        self._pwm_id = int(pwm_id)
        self._frequency_hz = int(frequency_hz)
        self._period_ns = 0
        self._duty_cycle_percent = 0.0
        self._started = False
        self._exported_here = False
        self._ensure_exported()
        self._set_period(self._frequency_hz)
        self.ChangeDutyCycle(0.0)

    @classmethod
    def from_board_pin(cls, board_pin, frequency_hz):
        gpio_pin_data = _import_gpio_pin_data()
        _, _, channel_data = gpio_pin_data.get_data()
        ch_info = channel_data["BOARD"].get(int(board_pin))
        if ch_info is None or ch_info.pwm_chip_dir is None or ch_info.pwm_id is None:
            raise RuntimeError(f"Board pin {board_pin} does not map to a PWM controller")
        return cls(ch_info.pwm_chip_dir, ch_info.pwm_id, frequency_hz)

    @property
    def _pwm_dir(self):
        return os.path.join(self._chip_dir, f"pwm{self._pwm_id}")

    def start(self, duty_cycle_percent):
        self.ChangeDutyCycle(duty_cycle_percent)
        self._write("enable", "1")
        self._started = True

    def stop(self):
        if not self._started and not os.path.exists(os.path.join(self._pwm_dir, "enable")):
            return
        self._write("enable", "0")
        self._started = False

    def ChangeFrequency(self, frequency_hz):
        frequency_hz = max(1, int(frequency_hz))
        if frequency_hz == self._frequency_hz:
            return
        was_started = self._started
        if was_started:
            self.stop()
        self._frequency_hz = frequency_hz
        self._set_period(frequency_hz)
        self.ChangeDutyCycle(self._duty_cycle_percent)
        if was_started:
            self.start(self._duty_cycle_percent)

    def ChangeDutyCycle(self, duty_cycle_percent):
        duty_cycle_percent = max(0.0, min(100.0, float(duty_cycle_percent)))
        self._duty_cycle_percent = duty_cycle_percent
        duty_ns = int(self._period_ns * (duty_cycle_percent / 100.0))
        self._write("duty_cycle", str(duty_ns))

    def cleanup(self):
        try:
            self.stop()
        except Exception:
            pass
        if self._exported_here:
            try:
                with open(os.path.join(self._chip_dir, "unexport"), "w", encoding="ascii") as handle:
                    handle.write(str(self._pwm_id))
            except Exception:
                pass

    def _ensure_exported(self):
        if os.path.isdir(self._pwm_dir):
            return
        with open(os.path.join(self._chip_dir, "export"), "w", encoding="ascii") as handle:
            handle.write(str(self._pwm_id))
        self._exported_here = True
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if os.path.isdir(self._pwm_dir):
                return
            time.sleep(0.02)
        raise RuntimeError(f"Timed out exporting PWM {self._pwm_id} from {self._chip_dir}")

    def _set_period(self, frequency_hz):
        self._period_ns = int(1_000_000_000 / int(frequency_hz))
        self._write("period", str(self._period_ns))

    def _write(self, name, value):
        with open(os.path.join(self._pwm_dir, name), "w", encoding="ascii") as handle:
            handle.write(value)


@dataclass(frozen=True)
class AudioAlertConfig:
    enabled: bool = False
    tone_pin: int = 15
    shutdown_pin: int = 29
    default_frequency_hz: int = 880
    allowed_codes: tuple[str, ...] = ("drowsiness_detected", "head_inattention_detected")
    queue_size: int = 8
    enabled_high: bool = True
    startup_muted: bool = True
    prefer_pwm: bool = True

    @classmethod
    def from_env(cls):
        allowed_codes_raw = os.getenv(
            "MP_AUDIO_ALERT_CODES",
            "drowsiness_detected,head_inattention_detected",
        )
        allowed_codes = tuple(
            code.strip() for code in allowed_codes_raw.split(",") if code.strip()
        )
        return cls(
            enabled=env_bool("MP_AUDIO_ENABLED", False),
            tone_pin=env_int("MP_AUDIO_TONE_PIN", 15),
            shutdown_pin=env_int("MP_AUDIO_SHUTDOWN_PIN", 29),
            default_frequency_hz=max(200, env_int("MP_AUDIO_DEFAULT_FREQUENCY_HZ", 880)),
            allowed_codes=allowed_codes or ("drowsiness_detected", "head_inattention_detected"),
            queue_size=max(1, env_int("MP_AUDIO_QUEUE_SIZE", 8)),
            enabled_high=env_bool("MP_AUDIO_SHUTDOWN_ACTIVE_HIGH", True),
            startup_muted=env_bool("MP_AUDIO_STARTUP_MUTED", True),
            prefer_pwm=env_bool("MP_AUDIO_PREFER_PWM", True),
        )


class AudioAlertNotifier:
    """Generate simple alert tones on a Jetson GPIO pin for the amp breakout."""

    def __init__(self, router, config):
        self.router = router
        self.config = config
        self._gpio = None
        self._queue = queue.Queue(maxsize=self.config.queue_size)
        self._thread = None
        self._stop_event = threading.Event()
        self._enabled = False
        self._pwm = None
        self._use_pwm = False

    def start(self):
        self._gpio = _import_gpio()
        try:
            self._gpio.setwarnings(False)
            self._gpio.setmode(self._gpio.BOARD)
            self._gpio.setup(
                self.config.shutdown_pin,
                self._gpio.OUT,
                initial=self._shutdown_level(enabled=not self.config.startup_muted),
            )
            self._setup_tone_output()
        except Exception as exc:
            raise RuntimeError(
                "Audio GPIO setup failed. Ensure the user can access /dev/gpiochip* "
                "or run after adding the user to the 'gpio' group."
            ) from exc

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._enabled = True
        self.router.emit_log(
            "Audio alert notifier started "
            f"tone_pin={self.config.tone_pin} shutdown_pin={self.config.shutdown_pin} "
            f"codes={','.join(self.config.allowed_codes)} "
            f"mode={'pwm' if self._use_pwm else 'gpio'}"
        )
        return True

    def stop(self):
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._silence()
        if self._gpio is not None:
            try:
                if self._use_pwm and self._pwm is not None:
                    self._pwm.cleanup()
                self._gpio.cleanup([self.config.tone_pin, self.config.shutdown_pin])
            except Exception:
                pass
        self._enabled = False

    def send_alert(self, level, message, code=None):
        if not self._enabled:
            return False
        if code and code not in self.config.allowed_codes:
            return False

        pattern = self._pattern_for(code=code, level=level)
        try:
            self._queue.put_nowait(pattern)
            return True
        except queue.Full:
            self.router.emit_log("Audio alert queue full; dropping tone", level="warning")
            return False

    def play_test_tone(self, frequency_hz=None, duration_s=3.0):
        if not self._enabled:
            return False

        frequency_hz = int(frequency_hz or self.config.default_frequency_hz)
        duration_s = max(0.05, float(duration_s))
        self._set_shutdown(True)
        time.sleep(0.02)
        try:
            self._tone(frequency_hz, duration_s)
        finally:
            self._silence()
        return True

    def _worker(self):
        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                break
            try:
                self._play_pattern(item)
            except Exception as exc:
                self.router.emit_log(f"Audio alert error: {exc}", level="error")
                self._silence()

    def _pattern_for(self, code, level):
        if code == "drowsiness_detected":
            return [
                {"freq": 880, "duration": 0.3},
                {"pause": 0.08},
                {"freq": 880, "duration": 0.3},
                {"pause": 0.08},
                {"freq": 660, "duration": 0.45},
            ]
        if code == "head_inattention_detected":
            return [
                {"freq": 660, "duration": 0.18},
                {"pause": 0.08},
                {"freq": 660, "duration": 0.18},
                {"pause": 0.08},
                {"freq": 660, "duration": 0.18},
            ]
        duration = 0.15 if level <= 1 else 0.25
        return [{"freq": self.config.default_frequency_hz, "duration": duration}]

    def _play_pattern(self, pattern):
        self._set_shutdown(True)
        time.sleep(0.01)
        for step in pattern:
            if self._stop_event.is_set():
                break
            if "pause" in step:
                self._silence()
                self._sleep_interruptibly(step["pause"])
                continue
            self._tone(step["freq"], step["duration"])
        self._silence()
        time.sleep(0.01)
        self._set_shutdown(False)

    def _tone(self, frequency_hz, duration_s):
        if self._use_pwm:
            self._pwm.ChangeFrequency(int(frequency_hz))
            self._pwm.start(50.0)
            self._sleep_interruptibly(duration_s)
            self._pwm.ChangeDutyCycle(0.0)
            self._pwm.stop()
            return

        half_period = 0.5 / float(frequency_hz)
        end_time = time.perf_counter() + float(duration_s)
        while time.perf_counter() < end_time and not self._stop_event.is_set():
            self._gpio.output(self.config.tone_pin, self._gpio.HIGH)
            self._busy_sleep(half_period)
            self._gpio.output(self.config.tone_pin, self._gpio.LOW)
            self._busy_sleep(half_period)

    def _busy_sleep(self, seconds):
        target = time.perf_counter() + max(0.0, seconds)
        while time.perf_counter() < target:
            pass

    def _sleep_interruptibly(self, seconds):
        self._stop_event.wait(max(0.0, seconds))

    def _set_shutdown(self, enabled):
        self._gpio.output(self.config.shutdown_pin, self._shutdown_level(enabled=enabled))

    def _shutdown_level(self, enabled):
        if self.config.enabled_high:
            return self._gpio.HIGH if enabled else self._gpio.LOW
        return self._gpio.LOW if enabled else self._gpio.HIGH

    def _silence(self):
        if self._gpio is None:
            return
        try:
            if self._use_pwm and self._pwm is not None:
                try:
                    self._pwm.ChangeDutyCycle(0.0)
                    self._pwm.stop()
                except Exception:
                    pass
            else:
                self._gpio.output(self.config.tone_pin, self._gpio.LOW)
            self._set_shutdown(False)
        except Exception:
            pass

    def _setup_tone_output(self):
        if self.config.prefer_pwm:
            try:
                self._pwm = _SysfsPWM.from_board_pin(
                    self.config.tone_pin,
                    self.config.default_frequency_hz,
                )
                self._use_pwm = True
                return
            except Exception:
                self._pwm = None
                self._use_pwm = False

        self._gpio.setup(self.config.tone_pin, self._gpio.OUT, initial=self._gpio.LOW)
