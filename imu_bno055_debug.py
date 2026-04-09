import time

from module_bno055 import BNO055
from module_imu_speed_monitor import IMUSpeedMonitorConfig


def main():
    config = IMUSpeedMonitorConfig.from_env()
    sensor = BNO055(
        bus=config.bus,
        address=config.address,
        use_external_crystal=config.use_external_crystal,
    )

    try:
        sensor.initialize()
        print(
            f"BNO055 connected on /dev/i2c-{config.bus} address=0x{config.address:02X} "
            f"use_external_crystal={config.use_external_crystal}"
        )
        while True:
            sample = sensor.read_sample()
            print(
                "linear="
                f"{sample.linear_acceleration} "
                "accel="
                f"{sample.acceleration} "
                "calib="
                f"{sample.calibration}"
            )
            time.sleep(1.0 / max(1, config.poll_hz))
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
