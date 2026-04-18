import socket


class UdpBleNotifier:
    """Send BLE alert commands to a local BLE bridge process."""

    def __init__(self, host="127.0.0.1", port=8766):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_alert(self, level: int, message: str):
        payload = f"{int(level)}|{message}".encode("utf-8", errors="ignore")
        try:
            self._sock.sendto(payload[:500], (self.host, self.port))
        except OSError:
            pass

    def stop(self):
        try:
            self._sock.close()
        except OSError:
            pass
