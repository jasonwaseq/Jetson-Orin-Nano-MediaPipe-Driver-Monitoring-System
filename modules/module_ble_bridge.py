import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


class UdpBleNotifier:
    """Send BLE alert commands to a local BLE bridge process."""

    def __init__(self, host="127.0.0.1", port=8766, auto_start=None):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._bridge_proc = None
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
            time.sleep(0.5)
            if self._bridge_proc.poll() is None:
                print(f"BLE bridge auto-started pid={self._bridge_proc.pid} log={log_path}")
            else:
                print(f"BLE bridge auto-start exited early; check {log_path}")
        except Exception as exc:
            if log_file is not None and not log_file.closed:
                log_file.close()
            self._bridge_proc = None
            print(f"BLE bridge auto-start failed: {exc}")

    def send_alert(self, level: int, message: str):
        clean_message = str(message).replace("\n", " ").replace("\r", " ")
        payload = f"{int(level)}|{clean_message}".encode("utf-8", errors="ignore")
        try:
            self._sock.sendto(payload[:500], (self.host, self.port))
            return True
        except OSError:
            return False

    def stop(self):
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
