# MediaPipe Driver Alert Uplink

This project runs face-based driver monitoring with MediaPipe and emits **alert events only**.

The Jetson always emits alerts over Bluetooth, even when the internet is down.
MQTT remains available for cloud relay architectures when connectivity exists:

`Jetson detector -> Bluetooth alert -> driver phone`

`Jetson detector -> MQTT uplink -> event consumers -> Postgres -> WebSocket gateway downlink`

No frame stream is sent by this script.

## What It Detects

- Eye-closure (drowsiness) events
- Head-inattention events

## Project Files

- `face_detect_mediapipe.py`: main camera detector + lifecycle wiring
- `module_event_router.py`: shared event fan-out to MQTT, BLE, and optional WebSocket sinks

## Requirements

- Python 3.10+
- Webcam (or update `VIDEO_SOURCE` in the script)
- BlueZ with BLE advertising support enabled on the Jetson
- Python packages:
  - `mediapipe`
  - `opencv-python`
  - `numpy`
  - `paho-mqtt`
  - `websockets` (optional, only if enabling local WS output)
  - `dbus-python` and `PyGObject` if `MP_BLE_ENABLED=1`

## BLE Bring-Up Notes

- BLE direct alerts are enabled by default with `MP_BLE_ENABLED=1`.
- The BLE server code registers a BlueZ GATT app and advertisement over the system D-Bus, so it often needs elevated privileges on the Jetson. If startup prints `BLE notifier failed to start: ...`, try launching the detector with `sudo` or disable BLE temporarily with `export MP_BLE_ENABLED=0`.
- A successful startup should print both `BLE GATT application registered` and `BLE advertisement registered as 'SleepyDrive'`. If you do not see those lines, the phone app will never discover `SleepyDrive`.

## Automatic Setup
An easy way to run the model is through the `model_initializer.sh` script

```
./model_initializer.sh
```

This script sets up the environment and runs the `face_detect_mediapipe.py` script on its own

## Manual Setup

```bash
python3 -m venv venv
./venv/bin/pip install mediapipe opencv-python numpy paho-mqtt websockets
```

## Run

```bash
./venv/bin/python face_detect_mediapipe.py
```

## Environment Variables

### Event metadata

- `MP_SOURCE_ID` (default: hostname)
- `MP_EVENT_PRODUCER` (default: `mediapipe-driver-monitor`)
- `MP_EVENT_SCHEMA_VERSION` (default: `1.0`)

### MQTT uplink (primary)

- `MP_MQTT_ENABLED` (default: `1`)
- `MP_MQTT_HOST` (default: `127.0.0.1`)
- `MP_MQTT_PORT` (default: `1883`)
- `MP_MQTT_TOPIC` (default: `sleepydrive/alerts/<source_id>`)
- `MP_MQTT_CLIENT_ID` (default: `uplink-<source_id>`)
- `MP_MQTT_USERNAME` (optional)
- `MP_MQTT_PASSWORD` (optional)
- `MP_MQTT_QOS` (default: `1`, allowed: `0..2`)
- `MP_MQTT_RETAIN` (default: `0`)
- `MP_MQTT_TLS` (default: `0`)
- `MP_MQTT_TLS_INSECURE` (default: `0`)
- `MP_MQTT_KEEPALIVE` (default: `60`)

Example:

```bash
export MP_SOURCE_ID=jetson-cam-01
export MP_MQTT_ENABLED=1
export MP_MQTT_HOST=73797b78ceac47e998c30ac034930c26.s1.eu.hivemq.cloud
export MP_MQTT_PORT=8883
export MP_MQTT_TOPIC=sleepydrive/alerts/jetson-cam-01
export MP_MQTT_CLIENT_ID=uplink-jetson-cam-01
export MP_MQTT_USERNAME=group7
export MP_MQTT_PASSWORD='replace_me'
export MP_MQTT_TLS=1
export MP_MQTT_QOS=1
export MP_MQTT_RETAIN=0
./venv/bin/python face_detect_mediapipe.py
```

Gateway consumer subscribe topic:

```text
sleepydrive/alerts/+
```

### Bluetooth alerts

- BLE alert delivery is enabled by default in the Jetson entrypoints.
- Bluetooth alerts are sent locally to subscribed phones and do not require internet access.
- If the Bluetooth adapter or BlueZ stack is unavailable, the script logs the failure and continues with the remaining sinks.

### Local WebSocket output (optional debug only)

- `MP_WS_ENABLED` (default: `0`)
- `MP_WS_HOST` (default: `0.0.0.0`)
- `MP_WS_PORT` (default: `8765`)

If you run a separate cloud WebSocket gateway service, keep this disabled.

## Event Payload Shape

Example `alert` payload:

```json
{
  "type": "alert",
  "event_type": "alert",
  "timestamp": "2026-03-04T16:00:00.000000+00:00",
  "event_id": "f39304f3-c4a7-4bcf-b212-952005d7fbb4",
  "event_version": "1.0",
  "source_id": "jetson-cam-01",
  "producer": "mediapipe-driver-monitor",
  "sequence": 42,
  "code": "drowsiness_detected",
  "message": "DROWSINESS DETECTED! Event #3 (eyes closed 1.6s)",
  "severity": "critical",
  "data": {
    "event_count": 3,
    "closed_duration_sec": 1.634
  }
}
```

`head_inattention_detected` uses the same envelope with corresponding `data` fields.

## Security Notes

- Keep broker addresses, credentials, and tokens out of source control.
- Use TLS (`MP_MQTT_TLS=1`) for cloud brokers.
- Scope broker ACLs by topic and client ID.
