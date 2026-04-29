import socket
import sys
import threading
from collections import deque
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
_MAX_RECENT_MESSAGES = 200


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
    recent_ids = deque(maxlen=_MAX_RECENT_MESSAGES)
    recent_id_set = set()
    try:
        while True:
            data, _addr = sock.recvfrom(1024)
            text = data.decode("utf-8", errors="ignore")
            message_id = None
            if text.startswith("v1|"):
                parts = text.split("|", 3)
                if len(parts) != 4:
                    continue
                _version, message_id, level_s, message = parts
            else:
                if "|" not in text:
                    continue
                level_s, message = text.split("|", 1)
            try:
                level = int(level_s)
            except ValueError:
                level = 1
            if message_id is None or message_id not in recent_id_set:
                notifier.send_alert(level, message)
                if message_id is not None:
                    if len(recent_ids) == recent_ids.maxlen:
                        recent_id_set.discard(recent_ids[0])
                    recent_ids.append(message_id)
                    recent_id_set.add(message_id)
            if message_id is not None:
                sock.sendto(f"v1|{message_id}|ok".encode("ascii"), _addr)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        notifier.stop()
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
