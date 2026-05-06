import uuid
from datetime import datetime

import pytest

import modules.module_event_router as module_event_router
from modules.module_event_router import EventRouter, severity_to_level


class DummySink:
    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)


class DummyDispatcher:
    def __init__(self):
        self.calls = []

    def publish_alert(self, **kwargs):
        self.calls.append(kwargs)
        return True


class DummyBle:
    def __init__(self):
        self.calls = []

    def send_alert(self, level, message):
        self.calls.append((level, message))
        return True


class DummySound:
    def __init__(self):
        self.calls = []

    def send_alert(self, level, message):
        self.calls.append((level, message))


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("info", 0),
        ("warning", 1),
        ("high", 2),
        ("critical", 2),
        ("unknown", 1),
        (None, 1),
    ],
)
def test_severity_to_level(severity, expected):
    assert severity_to_level(severity) == expected


def test_emit_event_adds_envelope_and_sequences(monkeypatch):
    monkeypatch.setattr(module_event_router, "utc_timestamp", lambda: "2026-05-06T00:00:00+00:00")
    monkeypatch.setattr(module_event_router.uuid, "uuid4", lambda: uuid.UUID(int=1))
    sink = DummySink()
    router = EventRouter("jetson-1", "producer", "1.0", sinks=[sink])

    first = router.emit_event("diagnostic", value=1)
    second = router.emit_event("diagnostic", value=2)

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["event_id"] == "00000000-0000-0000-0000-000000000001"
    assert first["timestamp"] == "2026-05-06T00:00:00+00:00"
    assert first["event_version"] == "1.0"
    assert first["source_id"] == "jetson-1"
    assert first["producer"] == "producer"
    assert sink.events == [first, second]
    datetime.fromisoformat(first["timestamp"])


def test_emit_alert_routes_to_all_configured_outputs(monkeypatch):
    monkeypatch.setattr(module_event_router, "utc_timestamp", lambda: "2026-05-06T00:00:00+00:00")
    sink = DummySink()
    dispatcher = DummyDispatcher()
    ble = DummyBle()
    sound = DummySound()
    logs = []
    router = EventRouter(
        "jetson-1",
        "producer",
        "1.0",
        dispatcher=dispatcher,
        ble_notifier=ble,
        sound_notifier=sound,
        sinks=[sink],
    )
    router.emit_log = lambda message, level="info", **data: logs.append((message, level, data))

    router.emit_alert("drowsiness_detected", "eyes closed", severity="critical", event_count=3)

    assert sink.events[0]["type"] == "alert"
    assert sink.events[0]["code"] == "drowsiness_detected"
    assert sink.events[0]["data"] == {"event_count": 3}
    assert dispatcher.calls == [
        {
            "level": 2,
            "message": "eyes closed",
            "metadata": {"code": "drowsiness_detected", "severity": "critical", "event_count": 3},
        }
    ]
    assert ble.calls == [(2, "eyes closed")]
    assert sound.calls == [(2, "eyes closed")]
    assert [entry[0] for entry in logs] == [
        "MQTT publish ok=True code=drowsiness_detected message=eyes closed",
        "BLE alert sent ok=True code=drowsiness_detected",
        "Sound alert sent code=drowsiness_detected",
    ]
