import socket
import sys
from pathlib import Path

for candidate in (
    Path("/usr/lib/python3/dist-packages"),
    Path("/usr/local/lib/python3/dist-packages"),
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))

from ble.ble_notifier import BLENotifier


def main():
    notifier = BLENotifier()
    if not notifier.start():
        return 1
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 8766))
    print("BLE bridge ready on 127.0.0.1:8766")
    try:
        while True:
            data, _addr = sock.recvfrom(1024)
            text = data.decode("utf-8", errors="ignore")
            if "|" not in text:
                continue
            level_s, message = text.split("|", 1)
            try:
                level = int(level_s)
            except ValueError:
                level = 1
            notifier.send_alert(level, message)
    except KeyboardInterrupt:
        pass
    finally:
        notifier.stop()
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
