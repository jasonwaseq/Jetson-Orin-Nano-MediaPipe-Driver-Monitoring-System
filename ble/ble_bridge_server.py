import socket
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for candidate in (
    Path("/usr/lib/python3/dist-packages"),
    Path("/usr/local/lib/python3/dist-packages"),
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))

from ble.ble_notifier import BLENotifier

_HEARTBEAT_INTERVAL = 10  # seconds


def _heartbeat_loop(notifier: BLENotifier, stop: threading.Event) -> None:
    """Send a keep-alive ping every _HEARTBEAT_INTERVAL seconds.

    Without periodic traffic the BlueZ link-layer supervision timeout can
    drop the BLE connection even though both sides are still alive.
    Level -1 is filtered out on the Flutter side and never shown in the UI.
    """
    while not stop.wait(timeout=_HEARTBEAT_INTERVAL):
        notifier.send_alert(-1, "ping")


def main():
    notifier = BLENotifier()
    if not notifier.start():
        return 1

    stop_event = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(notifier, stop_event),
        daemon=True,
        name="ble-heartbeat",
    )
    hb_thread.start()

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
        stop_event.set()
        notifier.stop()
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
