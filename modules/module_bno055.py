import fcntl
import math
import os
import struct
import time
from dataclasses import dataclass


I2C_SLAVE = 0x0703

BNO055_CHIP_ID = 0xA0
BNO055_DEFAULT_ADDRESS = 0x28

REG_CHIP_ID = 0x00
REG_PAGE_ID = 0x07
REG_ACCEL_DATA = 0x08
REG_LINEAR_ACCEL_DATA = 0x28
REG_CALIB_STAT = 0x35
REG_OPR_MODE = 0x3D
REG_PWR_MODE = 0x3E
REG_SYS_STATUS = 0x39
REG_SYS_TRIGGER = 0x3F

POWER_MODE_NORMAL = 0x00
MODE_CONFIG = 0x00
MODE_NDOF = 0x0C

ACCEL_SCALE_MPS2 = 1.0 / 100.0


class BNO055Error(RuntimeError):
    """Raised when the BNO055 cannot be accessed or initialized."""


@dataclass(frozen=True)
class BNO055Sample:
    timestamp_monotonic: float
    acceleration: tuple[float, float, float]
    linear_acceleration: tuple[float, float, float]
    calibration: dict[str, int]

    @property
    def linear_acceleration_magnitude(self):
        x, y, z = self.linear_acceleration
        return math.sqrt((x * x) + (y * y) + (z * z))


class LinuxI2CDevice:
    """Minimal Linux i2c-dev wrapper without external Python dependencies."""

    def __init__(self, bus, address):
        self.bus = int(bus)
        self.address = int(address)
        self.path = f"/dev/i2c-{self.bus}"
        self._fd = None

    def open(self):
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self.path, os.O_RDWR)
            fcntl.ioctl(self._fd, I2C_SLAVE, self.address)
        except OSError as exc:
            self.close()
            raise BNO055Error(
                f"Failed to open I2C bus '{self.path}' at address 0x{self.address:02X}: {exc}"
            ) from exc

    def close(self):
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        finally:
            self._fd = None

    def write(self, register, payload):
        self.open()
        data = bytes([register]) + bytes(payload)
        try:
            os.write(self._fd, data)
        except OSError as exc:
            raise BNO055Error(
                f"I2C write failed on '{self.path}' register 0x{register:02X}: {exc}"
            ) from exc

    def read(self, register, length):
        self.open()
        try:
            os.write(self._fd, bytes([register]))
            return os.read(self._fd, length)
        except OSError as exc:
            raise BNO055Error(
                f"I2C read failed on '{self.path}' register 0x{register:02X}: {exc}"
            ) from exc


class BNO055:
    """Minimal BNO055 I2C driver focused on acceleration-based alerting."""

    def __init__(self, bus=7, address=BNO055_DEFAULT_ADDRESS, use_external_crystal=True):
        self.bus = int(bus)
        self.address = int(address)
        self.use_external_crystal = bool(use_external_crystal)
        self._device = LinuxI2CDevice(bus=self.bus, address=self.address)

    def open(self):
        self._device.open()

    def close(self):
        self._device.close()

    def initialize(self):
        self.open()

        chip_id = self._read_u8(REG_CHIP_ID)
        if chip_id != BNO055_CHIP_ID:
            raise BNO055Error(
                f"BNO055 not detected on {self._device.path} address 0x{self.address:02X}: "
                f"chip_id=0x{chip_id:02X}"
            )

        self._write_u8(REG_PAGE_ID, 0x00)
        self._set_mode(MODE_CONFIG)
        self._write_u8(REG_PWR_MODE, POWER_MODE_NORMAL)
        self._write_u8(REG_SYS_TRIGGER, 0x00)
        time.sleep(0.05)

        self._set_mode(MODE_NDOF)
        time.sleep(0.05)

        if self.use_external_crystal:
            self._enable_external_crystal()
            time.sleep(0.05)

        self._ensure_mode(MODE_NDOF)

    def read_sample(self):
        return BNO055Sample(
            timestamp_monotonic=time.monotonic(),
            acceleration=self._read_vector(REG_ACCEL_DATA, ACCEL_SCALE_MPS2),
            linear_acceleration=self._read_vector(REG_LINEAR_ACCEL_DATA, ACCEL_SCALE_MPS2),
            calibration=self.read_calibration_status(),
        )

    def read_calibration_status(self):
        value = self._read_u8(REG_CALIB_STAT)
        return {
            "system": (value >> 6) & 0x03,
            "gyro": (value >> 4) & 0x03,
            "accel": (value >> 2) & 0x03,
            "mag": value & 0x03,
        }

    def _read_vector(self, register, scale):
        raw = self._device.read(register, 6)
        x, y, z = struct.unpack("<hhh", raw)
        return (
            round(x * scale, 4),
            round(y * scale, 4),
            round(z * scale, 4),
        )

    def _read_u8(self, register):
        raw = self._device.read(register, 1)
        if len(raw) != 1:
            raise BNO055Error(
                f"Expected 1 byte from register 0x{register:02X}, received {len(raw)} bytes"
            )
        return raw[0]

    def _write_u8(self, register, value):
        self._device.write(register, [value & 0xFF])

    def _set_mode(self, mode):
        self._write_u8(REG_OPR_MODE, MODE_CONFIG)
        time.sleep(0.02)
        if mode != MODE_CONFIG:
            self._write_u8(REG_OPR_MODE, mode)
            time.sleep(0.01)

    def _enable_external_crystal(self):
        current_mode = self._read_u8(REG_OPR_MODE) & 0x0F
        self._set_mode(MODE_CONFIG)
        self._write_u8(REG_PAGE_ID, 0x00)
        self._write_u8(REG_SYS_TRIGGER, 0x80)
        time.sleep(0.02)
        self._set_mode(current_mode if current_mode != MODE_CONFIG else MODE_NDOF)

    def _ensure_mode(self, expected_mode):
        current_mode = self._read_u8(REG_OPR_MODE) & 0x0F
        if current_mode == expected_mode:
            return

        # Some BNO055 units stay in CONFIG after trigger updates; force the final mode again.
        self._write_u8(REG_PAGE_ID, 0x00)
        self._write_u8(REG_SYS_TRIGGER, 0x00)
        time.sleep(0.02)
        self._set_mode(expected_mode)
        time.sleep(0.05)

        current_mode = self._read_u8(REG_OPR_MODE) & 0x0F
        if current_mode != expected_mode:
            sys_status = self._read_u8(REG_SYS_STATUS)
            raise BNO055Error(
                f"BNO055 failed to enter mode 0x{expected_mode:02X}; "
                f"current_mode=0x{current_mode:02X} sys_status=0x{sys_status:02X}"
            )
