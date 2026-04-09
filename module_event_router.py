import threading
import uuid

from module_env_init import utc_timestamp


def severity_to_level(severity):
    """Map string severities to numeric dispatcher levels."""
    mapping = {
        "info": 0,
        "warning": 1,
        "high": 2,
        "critical": 2,
    }
    return mapping.get(str(severity).lower(), 1)


class EventRouter:
    """Route structured events to local sinks, MQTT, and BLE."""

    def __init__(
        self,
        source_id,
        producer,
        schema_version,
        dispatcher=None,
        ble_notifier=None,
        sound_notifier=None,
        sinks=None,
    ):
        self.source_id = source_id
        self.producer = producer
        self.schema_version = schema_version
        self.dispatcher = dispatcher
        self.ble_notifier = ble_notifier
        self.sound_notifier = sound_notifier
        self.sinks = list(sinks or [])
        self._event_sequence = 0
        self._event_sequence_lock = threading.Lock()

    def add_sink(self, sink):
        self.sinks.append(sink)

    def emit_event(self, event_type, **payload):
        """Emit a structured event to all configured sinks."""
        with self._event_sequence_lock:
            self._event_sequence += 1
            sequence = self._event_sequence

        event = {
            "type": event_type,
            "event_type": event_type,
            "timestamp": utc_timestamp(),
            "event_id": str(uuid.uuid4()),
            "event_version": self.schema_version,
            "source_id": self.source_id,
            "producer": self.producer,
            "sequence": sequence,
            **payload,
        }
        for sink in self.sinks:
            sink.send(event)
        return event

    def emit_log(self, message, level="info", **data):
        """Print log data locally."""
        print(message)

    def emit_alert(self, code, message, severity="warning", **data):
        """Emit alert payload to all configured event sinks."""
        payload = {"code": code, "message": message, "severity": severity}
        if data:
            payload["data"] = data
        self.emit_event("alert", **payload)

        level = severity_to_level(severity)
        if self.dispatcher is not None:
            metadata = {"code": code, "severity": severity}
            metadata.update(data)
            ok = self.dispatcher.publish_alert(
                level=level,
                message=message,
                metadata=metadata,
            )
            self.emit_log(f"MQTT publish ok={ok} code={code} message={message}")

        if self.ble_notifier is not None:
            self.ble_notifier.send_alert(level, message)
            self.emit_log(f"BLE alert sent code={code}")

        if self.sound_notifier is not None:
            ok = self.sound_notifier.send_alert(level, message, code=code)
            if ok:
                self.emit_log(f"Audio alert sent code={code}")
