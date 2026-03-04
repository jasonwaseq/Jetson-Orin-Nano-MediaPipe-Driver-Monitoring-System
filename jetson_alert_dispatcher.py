import json
import os
import threading
import time
from datetime import datetime, timezone


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def _env_first(names, default=None):
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def _env_bool(names, default=False):
    value = _env_first(names)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(names, default):
    value = _env_first(names)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _derive_status_topic(alert_topic, source_id):
    # sleepydrive/alerts/jetson-01 -> sleepydrive/status/jetson-01
    if "/alerts/" in alert_topic:
        return alert_topic.replace("/alerts/", "/status/")
    if alert_topic.endswith("/alerts"):
        return alert_topic[:-7] + "/status/" + source_id
    return f"sleepydrive/status/{source_id}"


def _normalize_publish_topic(topic, source_id, fallback_prefix):
    t = (topic or "").strip()
    if not t:
        return f"{fallback_prefix}/{source_id}"
    if "+" in t or "#" in t:
        # Publishing to wildcards is invalid; convert to exact topic.
        if t.endswith("/+"):
            return t[:-2] + f"/{source_id}"
        if t.endswith("/#"):
            return t[:-2] + f"/{source_id}"
        return f"{fallback_prefix}/{source_id}"
    return t


class JetsonAlertDispatcher:
    """MQTT alert dispatcher for Jetson detector events, with presence + heartbeat."""

    def __init__(
        self,
        source_id,
        host,
        port,
        topic,
        status_topic,
        client_id,
        username=None,
        password=None,
        qos=1,
        retain=False,
        use_tls=False,
        tls_insecure=False,
        keepalive=60,
        source="jetson",
        heartbeat_seconds=10,
    ):
        self.source_id = source_id
        self.host = host
        self.port = port
        self.topic = topic
        self.status_topic = status_topic
        self.client_id = client_id
        self.username = username
        self.password = password
        self.qos = qos
        self.retain = retain
        self.use_tls = use_tls
        self.tls_insecure = tls_insecure
        self.keepalive = keepalive
        self.source = source

        self.client = None
        self._mqtt_mod = None
        self._enabled = False
        self._connect_event = threading.Event()
        self._connect_rc = None

        self.heartbeat_seconds = max(5, int(heartbeat_seconds))
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    @classmethod
    def from_env(cls):
        source_id = _env_first(["MP_SOURCE_ID"], "jetson-01")

        default_alert_topic = f"sleepydrive/alerts/{source_id}"
        topic = _env_first(
            ["MP_MQTT_TOPIC", "MP_QTT_TOPIC", "MPMQTT_TOPIC", "MPQTT_TOPIC"],
            default_alert_topic,
        )
        if topic != default_alert_topic:
            print(
                f"JetsonAlertDispatcher topic override requested ('{topic}'); "
                f"using required topic '{default_alert_topic}'."
            )
            topic = default_alert_topic

        raw_status_topic = _env_first(
            ["MP_MQTT_STATUS_TOPIC", "MP_QTT_STATUS_TOPIC", "MQTT_STATUS_TOPICS"],
            _derive_status_topic(topic, source_id),
        )
        status_topic = _normalize_publish_topic(
            raw_status_topic, source_id=source_id, fallback_prefix="sleepydrive/status"
        )

        return cls(
            source_id=source_id,
            host=_env_first(["MP_MQTT_HOST", "MP_QTT_HOST", "MPMQTT_HOST", "MPQTT_HOST"], "127.0.0.1"),
            port=_env_int(["MP_MQTT_PORT", "MP_QTT_PORT", "MPMQTT_PORT", "MPQTT_PORT"], 1883),
            topic=topic,
            status_topic=status_topic,
            client_id=_env_first(
                ["MP_MQTT_CLIENT_ID", "MP_QTT_CLIENT_ID", "MPMQTT_CLIENT_ID", "MPQTT_CLIENT_ID"],
                f"uplink-{source_id}",
            ),
            username=_env_first(["MP_MQTT_USERNAME", "MP_QTT_USERNAME", "MPMQTT_USERNAME", "MPQTT_USERNAME"]),
            password=_env_first(["MP_MQTT_PASSWORD", "MP_QTT_PASSWORD", "MPMQTT_PASSWORD", "MPQTT_PASSWORD"]),
            qos=max(0, min(2, _env_int(["MP_MQTT_QOS", "MP_QTT_QOS", "MPMQTT_QOS", "MPQTT_QOS"], 1))),
            retain=_env_bool(["MP_MQTT_RETAIN", "MP_QTT_RETAIN", "MPMQTT_RETAIN", "MPQTT_RETAIN"], False),
            use_tls=_env_bool(["MP_MQTT_TLS", "MP_QTT_TLS", "MPMQTT_TLS", "MPQTT_TLS"], False),
            tls_insecure=_env_bool(
                ["MP_MQTT_TLS_INSECURE", "MP_QTT_TLS_INSECURE", "MPMQTT_TLS_INSECURE", "MPQTT_TLS_INSECURE"],
                False,
            ),
            keepalive=_env_int(["MP_MQTT_KEEPALIVE", "MP_QTT_KEEPALIVE", "MPMQTT_KEEPALIVE", "MPQTT_KEEPALIVE"], 60),
            source=_env_first(["MP_SOURCE", "SOURCE"], "jetson"),
            heartbeat_seconds=_env_int(
                ["MP_MQTT_HEARTBEAT_SECONDS", "MP_QTT_HEARTBEAT_SECONDS", "MPMQTT_HEARTBEAT_SECONDS", "MPQTT_HEARTBEAT_SECONDS"],
                10,
            ),
        )

    def connect(self):
        try:
            import paho.mqtt.client as mqtt  # pylint: disable=import-outside-toplevel
            self._mqtt_mod = mqtt
        except ImportError:
            print("JetsonAlertDispatcher disabled: install dependency with 'pip install paho-mqtt'")
            return False

        try:
            self.client = self._mqtt_mod.Client(
                client_id=self.client_id,
                protocol=self._mqtt_mod.MQTTv311,
            )
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect

            if self.username:
                self.client.username_pw_set(self.username, self.password)
            if self.use_tls:
                self.client.tls_set()
                if self.tls_insecure:
                    self.client.tls_insecure_set(True)

            offline_payload = json.dumps(
                {
                    "type": "presence",
                    "source_id": self.source_id,
                    "online": False,
                    "source": self.source,
                    "event_ts": _utc_timestamp(),
                    "metadata": {"state": "lwt"},
                },
                separators=(",", ":"),
            )
            self.client.will_set(self.status_topic, payload=offline_payload, qos=self.qos, retain=True)

            self._connect_rc = None
            self._connect_event.clear()
            self._enabled = False

            self.client.connect(self.host, self.port, keepalive=self.keepalive)
            self.client.loop_start()
        except Exception as exc:
            print(f"JetsonAlertDispatcher connect failed: {exc}")
            return False

        if not self._connect_event.wait(timeout=8.0):
            print("JetsonAlertDispatcher connect timeout waiting for MQTT CONNACK")
            self.close()
            return False
        if self._connect_rc != 0:
            print(f"JetsonAlertDispatcher MQTT connection rejected rc={self._connect_rc}")
            self.close()
            return False

        self.publish_presence(online=True, metadata={"state": "connected"}, retain=True)
        self._start_heartbeat()
        print(
            f"JetsonAlertDispatcher connected host={self.host}:{self.port} "
            f"topic='{self.topic}' status_topic='{self.status_topic}' "
            f"source_id='{self.source_id}' tls={self.use_tls} heartbeat={self.heartbeat_seconds}s"
        )
        return True

    def publish_alert(self, level, message, metadata=None):
        if not self._enabled or self.client is None:
            return False

        payload = {
            "type": "alert",
            "event_type": "alert",
            "timestamp": _utc_timestamp(),
            "source_id": self.source_id,
            "device_id": self.source_id,
            "level": level,
            "message": message,
            "metadata": metadata or {},
        }
        encoded_payload = json.dumps(payload, separators=(",", ":"))

        try:
            result = self.client.publish(
                self.topic,
                payload=encoded_payload,
                qos=self.qos,
                retain=self.retain,
            )
            ok = result.rc == self._mqtt_mod.MQTT_ERR_SUCCESS
            print(
                f"publish_alert rc={result.rc} ok={ok} mid={result.mid} "
                f"topic='{self.topic}' source_id='{self.source_id}' level={level}"
            )
            return ok
        except Exception as exc:
            print(f"publish_alert exception: {exc}")
            return False

    def publish_presence(self, online, metadata=None, retain=True):
        if not self._enabled or self.client is None:
            return False

        payload = {
            "type": "presence",
            "source_id": self.source_id,
            "online": bool(online),
            "source": self.source,
            "event_ts": _utc_timestamp(),
            "metadata": metadata or {},
        }
        encoded_payload = json.dumps(payload, separators=(",", ":"))

        try:
            result = self.client.publish(
                self.status_topic,
                payload=encoded_payload,
                qos=self.qos,
                retain=retain,
            )
            ok = result.rc == self._mqtt_mod.MQTT_ERR_SUCCESS
            print(
                f"publish_presence rc={result.rc} ok={ok} mid={result.mid} "
                f"topic='{self.status_topic}' source_id='{self.source_id}' online={online}"
            )
            return ok
        except Exception as exc:
            print(f"publish_presence exception: {exc}")
            return False

    def publish_heartbeat(self):
        if not self._enabled or self.client is None:
            return False

        payload = {
            "type": "heartbeat",
            "source_id": self.source_id,
            "online": True,
            "source": self.source,
            "event_ts": _utc_timestamp(),
        }
        encoded_payload = json.dumps(payload, separators=(",", ":"))

        try:
            result = self.client.publish(
                self.status_topic,
                payload=encoded_payload,
                qos=self.qos,
                retain=False,
            )
            ok = result.rc == self._mqtt_mod.MQTT_ERR_SUCCESS
            print(
                f"publish_heartbeat rc={result.rc} ok={ok} mid={result.mid} "
                f"topic='{self.status_topic}' source_id='{self.source_id}'"
            )
            return ok
        except Exception as exc:
            print(f"publish_heartbeat exception: {exc}")
            return False

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(self.heartbeat_seconds):
            if not self._enabled or self.client is None:
                continue
            self.publish_heartbeat()

    def _start_heartbeat(self):
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self):
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def close(self):
        if self.client is None:
            return
        try:
            self._stop_heartbeat()
            if self._enabled:
                self.publish_presence(online=False, metadata={"state": "closed"}, retain=True)
                time.sleep(0.1)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        finally:
            self._enabled = False
            self.client = None
            self._connect_event.clear()
            self._connect_rc = None

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        self._connect_rc = rc
        self._enabled = rc == 0
        self._connect_event.set()
        print(f"JetsonAlertDispatcher MQTT connected rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self._enabled = False
        print(f"JetsonAlertDispatcher MQTT disconnected rc={rc}")
