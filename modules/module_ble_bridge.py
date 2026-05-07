import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import Lock


def _env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


class UdpBleNotifier:
    """Send BLE alert commands to a local BLE bridge process."""

    def __init__(self, host="127.0.0.1", port=8766, auto_start=None, enabled=None):
        self.host = host
        self.port = port
        self.enabled = _env_bool("MP_BLE_ENABLED", True) if enabled is None else enabled
        self._sock = None
        self._send_lock = Lock()
        self._bridge_proc = None
        if not self.enabled:
            print("BLE alerts disabled by MP_BLE_ENABLED=0")
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(float(os.getenv("MP_BLE_BRIDGE_ACK_TIMEOUT", "0.2")))
        self._failure_backoff_sec = float(os.getenv("MP_BLE_BRIDGE_FAILURE_BACKOFF_SEC", "10.0"))
        self._next_attempt_time = 0.0
        self._consecutive_failures = 0
        if auto_start is None:
            auto_start = _env_bool("MP_BLE_BRIDGE_AUTOSTART", True)
        if auto_start and not self._bridge_port_in_use():
            self._start_bridge_process()

    def _bridge_port_in_use(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind((self.host, self.port))
            return False
        except OSError:
            return True
        finally:
            probe.close()

    def _start_bridge_process(self):
        project_root = Path(__file__).resolve().parent.parent
        log_path = os.getenv("MP_BLE_BRIDGE_LOG", "/tmp/ble_bridge.log")
        log_file = None
        try:
            log_file = open(log_path, "ab", buffering=0)
            self._bridge_proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "ble.ble_bridge_server"],
                cwd=str(project_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log_file.close()
            start_timeout = float(os.getenv("MP_BLE_BRIDGE_START_TIMEOUT", "12.0"))
            deadline = time.monotonic() + start_timeout
            while self._bridge_proc.poll() is None and time.monotonic() < deadline:
                if self._bridge_port_in_use():
                    break
                time.sleep(0.1)
            if self._bridge_proc.poll() is None and self._bridge_port_in_use():
                print(f"BLE bridge auto-started pid={self._bridge_proc.pid} log={log_path}")
            elif self._bridge_proc.poll() is None:
                print(
                    f"BLE bridge auto-started pid={self._bridge_proc.pid} "
                    f"but port {self.host}:{self.port} is not ready; check {log_path}"
                )
            else:
                print(f"BLE bridge auto-start exited early; check {log_path}")
        except Exception as exc:
            if log_file is not None and not log_file.closed:
                log_file.close()
            self._bridge_proc = None
            print(f"BLE bridge auto-start failed: {exc}")

    def send_alert(self, level: int, message: str):
        if not self.enabled or self._sock is None:
            return False
        now = time.monotonic()
        if now < self._next_attempt_time:
            return False

        clean_message = str(message).replace("\n", " ").replace("\r", " ")
        message_id = uuid.uuid4().hex
        payload = f"v1|{message_id}|{int(level)}|{clean_message}".encode(
            "utf-8",
            errors="ignore",
        )
        expected_ack = f"v1|{message_id}|ok".encode("ascii")
        retries = max(1, int(os.getenv("MP_BLE_BRIDGE_SEND_ATTEMPTS", "1")))

        with self._send_lock:
            for _attempt in range(retries):
                try:
                    self._sock.sendto(payload[:500], (self.host, self.port))
                    response, _addr = self._sock.recvfrom(128)
                    if response == expected_ack:
                        self._consecutive_failures = 0
                        self._next_attempt_time = 0.0
                        return True
                except (OSError, socket.timeout):
                    continue
        self._consecutive_failures += 1
        self._next_attempt_time = time.monotonic() + self._failure_backoff_sec
        return False

    def stop(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._bridge_proc is not None and self._bridge_proc.poll() is None:
            try:
                self._bridge_proc.terminate()
                self._bridge_proc.wait(timeout=3)
            except Exception:
                pass
