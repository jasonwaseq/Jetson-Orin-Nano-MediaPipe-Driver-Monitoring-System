import socket
from modules.module_alarm import Alarm


class UdpSoundNotifier:
    """Send sound alert commands to a local audio bridge process."""

    def __init__(self, host="127.0.0.1", port=8767):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._fallback_alarm = None

    def send_alert(self, level: int, message: str):
        payload = f"{int(level)}|{message}".encode("utf-8", errors="ignore")
        try:
            self._sock.sendto(payload[:500], (self.host, self.port))
        except OSError:
            # If the bridge is unavailable, play the alarm locally rather than dropping it.
            if self._fallback_alarm is None:
                self._fallback_alarm = Alarm()
            self._fallback_alarm.play_once()

    def stop(self):
        try:
            self._sock.close()
        except OSError:
            pass
