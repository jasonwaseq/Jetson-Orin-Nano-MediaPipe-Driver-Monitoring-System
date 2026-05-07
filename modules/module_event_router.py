import os
import queue
import threading
import uuid

from modules.module_env_init import utc_timestamp


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
        async_delivery=False,
        delivery_queue_size=None,
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
        self._async_delivery = bool(async_delivery)
        queue_size = delivery_queue_size
        if queue_size is None:
            queue_size = int(os.getenv("MP_EVENT_DELIVERY_QUEUE_SIZE", "128"))
        self._delivery_queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._delivery_stop = threading.Event()
        self._delivery_thread = None
        self.dropped_deliveries = 0
        if self._async_delivery:
            self._start_delivery_worker()

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
        delivery = (code, message, severity, level, dict(data))
        if self._async_delivery:
            self._enqueue_delivery(delivery)
            return

        self._deliver_alert(*delivery)

    def stop(self):
        """Stop background alert delivery after draining queued work briefly."""
        if not self._async_delivery:
            return
        self._delivery_stop.set()
        try:
            self._delivery_queue.put_nowait(None)
        except queue.Full:
            try:
                self._delivery_queue.get_nowait()
            except queue.Empty:
                pass
            self._delivery_queue.put_nowait(None)
        if self._delivery_thread is not None:
            self._delivery_thread.join(timeout=3.0)

    def _start_delivery_worker(self):
        if self._delivery_thread is not None and self._delivery_thread.is_alive():
            return
        self._delivery_stop.clear()
        self._delivery_thread = threading.Thread(
            target=self._delivery_loop,
            daemon=True,
            name="event-delivery",
        )
        self._delivery_thread.start()

    def _enqueue_delivery(self, delivery):
        try:
            self._delivery_queue.put_nowait(delivery)
            return
        except queue.Full:
            pass

        try:
            self._delivery_queue.get_nowait()
            self.dropped_deliveries += 1
        except queue.Empty:
            pass
        try:
            self._delivery_queue.put_nowait(delivery)
        except queue.Full:
            self.dropped_deliveries += 1

    def _delivery_loop(self):
        while True:
            try:
                item = self._delivery_queue.get(timeout=0.2)
            except queue.Empty:
                if self._delivery_stop.is_set():
                    break
                continue
            if item is None:
                break
            self._deliver_alert(*item)

    def _deliver_alert(self, code, message, severity, level, data):
        if self.dispatcher is not None:
            metadata = {"code": code, "severity": severity}
            metadata.update(data)
            try:
                ok = self.dispatcher.publish_alert(
                    level=level,
                    message=message,
                    metadata=metadata,
                )
                self.emit_log(f"MQTT publish ok={ok} code={code} message={message}")
            except Exception as exc:
                self.emit_log(f"MQTT publish exception code={code}: {exc}", level="warning")

        if self.ble_notifier is not None:
            try:
                ok = self.ble_notifier.send_alert(level, message)
                self.emit_log(f"BLE alert sent ok={ok} code={code}")
            except Exception as exc:
                self.emit_log(f"BLE alert exception code={code}: {exc}", level="warning")

        if self.sound_notifier is not None:
            try:
                self.sound_notifier.send_alert(level, message)
                self.emit_log(f"Sound alert sent code={code}")
            except Exception as exc:
                self.emit_log(f"Sound alert exception code={code}: {exc}", level="warning")
