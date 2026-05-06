import json

import pytest

import modules.jetson_alert_dispatcher as dispatcher_module
from modules.jetson_alert_dispatcher import (
    JetsonAlertDispatcher,
    _derive_status_topic,
    _env_bool,
    _env_first,
    _env_int,
    _normalize_publish_topic,
)


def test_env_helpers_prefer_first_non_empty_and_parse_types(monkeypatch):
    monkeypatch.setenv("FIRST", "")
    monkeypatch.setenv("SECOND", "1884")

    assert _env_first(["FIRST", "SECOND"], "fallback") == "1884"
    assert _env_int(["FIRST", "SECOND"], 1883) == 1884
    assert _env_bool(["FIRST", "SECOND"], False) is True

    monkeypatch.setenv("SECOND", "bad")
    assert _env_int(["FIRST", "SECOND"], 1883) == 1883
    assert _env_bool(["MISSING"], True) is True


@pytest.mark.parametrize(
    ("topic", "source_id", "expected"),
    [
        ("sleepydrive/alerts/jetson-01", "jetson-01", "sleepydrive/status/jetson-01"),
        ("fleet/alerts", "jetson-02", "fleet/status/jetson-02"),
        ("custom/topic", "jetson-03", "sleepydrive/status/jetson-03"),
    ],
)
def test_derive_status_topic(topic, source_id, expected):
    assert _derive_status_topic(topic, source_id) == expected


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("", "sleepydrive/status/jetson-1"),
        ("sleepydrive/status/+", "sleepydrive/status/jetson-1"),
        ("sleepydrive/status/#", "sleepydrive/status/jetson-1"),
        ("bad/+/topic", "sleepydrive/status/jetson-1"),
        ("exact/topic", "exact/topic"),
    ],
)
def test_normalize_publish_topic(topic, expected):
    assert _normalize_publish_topic(topic, source_id="jetson-1", fallback_prefix="sleepydrive/status") == expected


def test_from_env_enforces_required_alert_topic_and_clamps_qos(monkeypatch):
    monkeypatch.setenv("MP_SOURCE_ID", "jetson-test")
    monkeypatch.setenv("MP_MQTT_TOPIC", "custom/topic")
    monkeypatch.setenv("MP_MQTT_STATUS_TOPIC", "sleepydrive/status/+")
    monkeypatch.setenv("MP_MQTT_QOS", "9")
    monkeypatch.setenv("MP_MQTT_RETAIN", "1")
    monkeypatch.setenv("MP_MQTT_TLS", "true")
    monkeypatch.setenv("MP_MQTT_TLS_INSECURE", "yes")

    dispatcher = JetsonAlertDispatcher.from_env()

    assert dispatcher.topic == "sleepydrive/alerts/jetson-test"
    assert dispatcher.status_topic == "sleepydrive/status/jetson-test"
    assert dispatcher.qos == 2
    assert dispatcher.retain is True
    assert dispatcher.use_tls is True
    assert dispatcher.tls_insecure is True


def test_publish_methods_queue_json_payloads(monkeypatch):
    monkeypatch.setattr(dispatcher_module, "_utc_timestamp", lambda: "2026-05-06T00:00:00+00:00")
    dispatcher = JetsonAlertDispatcher(
        source_id="jetson-1",
        host="localhost",
        port=1883,
        topic="alerts/topic",
        status_topic="status/topic",
        client_id="client",
        source="unit-test",
    )
    dispatcher._enabled = True
    dispatcher.client = object()

    assert dispatcher.publish_alert(2, "eyes closed", metadata={"code": "drowsiness"}) is True
    assert dispatcher.publish_presence(True, metadata={"state": "connected"}, retain=True) is True
    assert dispatcher.publish_heartbeat() is True

    alert = dispatcher._publish_queue.get_nowait()
    presence = dispatcher._publish_queue.get_nowait()
    heartbeat = dispatcher._publish_queue.get_nowait()

    assert alert[0] == "alert"
    assert alert[2:] == ("alerts/topic", 1, False)
    assert json.loads(alert[1]) == {
        "type": "alert",
        "event_type": "alert",
        "timestamp": "2026-05-06T00:00:00+00:00",
        "source_id": "jetson-1",
        "device_id": "jetson-1",
        "level": 2,
        "message": "eyes closed",
        "metadata": {"code": "drowsiness"},
    }
    assert presence[0] == "presence"
    assert presence[2:] == ("status/topic", 1, True)
    assert json.loads(presence[1])["online"] is True
    assert heartbeat[0] == "heartbeat"
    assert heartbeat[2:] == ("status/topic", 1, False)


def test_publish_methods_return_false_when_disabled():
    dispatcher = JetsonAlertDispatcher(
        source_id="jetson-1",
        host="localhost",
        port=1883,
        topic="alerts/topic",
        status_topic="status/topic",
        client_id="client",
    )

    assert dispatcher.publish_alert(1, "message") is False
    assert dispatcher.publish_presence(True) is False
    assert dispatcher.publish_heartbeat() is False
