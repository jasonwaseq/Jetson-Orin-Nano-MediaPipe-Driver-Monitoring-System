event_sinks = []
event_sequence = 0

def emit_event(event_type, **payload):
    """Emit a structured event to all configured sinks."""
    if not event_sinks:
        return
    global event_sequence
    with event_sequence_lock:
        event_sequence += 1
        sequence = event_sequence
    event = {
        "type": event_type,
        "event_type": event_type,
        "timestamp": utc_timestamp(),
        "event_id": str(uuid.uuid4()),
        "event_version": EVENT_SCHEMA_VERSION,
        "source_id": EVENT_SOURCE_ID,
        "producer": EVENT_PRODUCER,
        "sequence": sequence,
        **payload,
    }
    for sink in event_sinks:
        sink.send(event)


def emit_log(message, level="info", **data):
    """Print log data locally."""
    print(message)


def severity_to_level(severity):
    """Map string severities to numeric dispatcher levels."""
    mapping = {
        "info": 0,
        "warning": 1,
        "high": 2,
        "critical": 2,
    }
    return mapping.get(str(severity).lower(), 1)


def emit_alert(code, message, dispatcher, ble_notifier, severity="warning", **data):
    """Emit alert payload to all configured event sinks."""
    payload = {"code": code, "message": message, "severity": severity}
    if data:
        payload["data"] = data
    emit_event("alert", **payload)
    level = severity_to_level(severity)
    if dispatcher is not None:
        metadata = {"code": code, "severity": severity}
        metadata.update(data)
        ok = dispatcher.publish_alert(
            level=level,
            message=message,
            metadata=metadata,
        )
        emit_log(f"MQTT publish ok={ok} code={code} message={message}")
    if ble_notifier is not None:
        ble_notifier.send_alert(level, message)
        emit_log(f"BLE alert sent code={code}")


