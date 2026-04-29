import socket
import threading

from modules.module_ble_bridge import UdpBleNotifier


def _unused_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def test_send_alert_returns_false_without_bridge(monkeypatch):
    monkeypatch.setenv("MP_BLE_BRIDGE_ACK_TIMEOUT", "0.05")
    monkeypatch.setenv("MP_BLE_BRIDGE_SEND_ATTEMPTS", "1")
    notifier = UdpBleNotifier(port=_unused_udp_port(), auto_start=False)
    try:
        assert notifier.send_alert(2, "drowsy") is False
    finally:
        notifier.stop()


def test_send_alert_returns_true_after_bridge_ack(monkeypatch):
    monkeypatch.setenv("MP_BLE_BRIDGE_ACK_TIMEOUT", "0.2")
    monkeypatch.setenv("MP_BLE_BRIDGE_SEND_ATTEMPTS", "1")
    port = _unused_udp_port()
    received = []
    ready = threading.Event()

    def server():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", port))
            ready.set()
            data, addr = sock.recvfrom(1024)
            text = data.decode("utf-8")
            received.append(text)
            _version, message_id, _level, _message = text.split("|", 3)
            sock.sendto(f"v1|{message_id}|ok".encode("ascii"), addr)
        finally:
            sock.close()

    thread = threading.Thread(target=server)
    thread.start()
    assert ready.wait(timeout=1)

    notifier = UdpBleNotifier(port=port, auto_start=False)
    try:
        assert notifier.send_alert(2, "drowsy") is True
    finally:
        notifier.stop()
        thread.join(timeout=1)

    assert len(received) == 1
    assert received[0].startswith("v1|")
    assert received[0].endswith("|2|drowsy")


def test_send_alert_returns_false_when_ble_disabled():
    notifier = UdpBleNotifier(port=_unused_udp_port(), auto_start=False, enabled=False)
    try:
        assert notifier.send_alert(2, "drowsy") is False
    finally:
        notifier.stop()
