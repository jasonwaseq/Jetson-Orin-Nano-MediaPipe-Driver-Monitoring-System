# MediaPipe Driver Alert Uplink

This project runs face-based driver monitoring with MediaPipe and emits **alert events only**.

Integration path is now MQTT-first for cloud relay architectures:

`Jetson detector -> MQTT uplink -> event consumers -> Postgres -> WebSocket gateway downlink`

No frame stream is sent by this script.

## What It Detects

- Eye-closure (drowsiness) events
- Head-inattention events
- BNO055 IMU acceleration-based speeding / harsh-acceleration proxy events
- Audible alert tones for drowsiness and head-inattention events

## Project Files

- `face_detect_mediapipe.py`: main camera detector + lifecycle wiring
- `module_event_router.py`: shared event fan-out to MQTT, BLE, and optional WebSocket sinks
- `module_bno055.py`: dependency-free Linux I2C BNO055 driver for the Jetson
- `module_imu_speed_monitor.py`: background IMU polling and speeding-alert logic
- `module_audio_alert.py`: GPIO-driven audio alert notifier for the amp breakout
- `tests/audio_alert_debug.py`: standalone audio alert tone test
- `imu_bno055_debug.py`: standalone IMU bring-up/debug script

## Requirements

- Python 3.10+
- Webcam (or update `VIDEO_SOURCE` in the script)
- Jetson Orin Nano with I2C enabled on the 40-pin header
- Adafruit BNO055 breakout
- Python packages:
  - `mediapipe`
  - `opencv-python`
  - `numpy`
  - `paho-mqtt`
  - `websockets` (optional, only if enabling local WS output)

## BNO055 Wiring

Use the 40-pin `J12` header on the Jetson Orin Nano dev kit.

| BNO055 pin | Jetson Orin Nano pin | Notes |
| --- | --- | --- |
| `VIN` | `Pin 1` (`3.3V`) | Use `3.3V`, not `5V`, so sensor power matches Jetson logic |
| `GND` | `Pin 6` (`GND`) | Common ground |
| `SDA` | `Pin 3` (`I2C1_SDA`) | Main I2C data line |
| `SCL` | `Pin 5` (`I2C1_SCL`) | Main I2C clock line |
| `ADR` | Leave open | Default I2C address stays `0x28` |
| `RST` | Leave open | Optional only; software init handles reset/config |
| `INT` | Leave open | Not used by this implementation |
| `PS0`, `PS1` | Leave open | Adafruit recommends leaving these unconnected for normal I2C mode |

Notes:

- NVIDIA documents the J12 header as `3.3V` logic, with `Pin 3 = I2C1_SDA` and `Pin 5 = I2C1_SCL`.
- Adafruit documents `VIN` as `3.3-5.0V`, `SDA/SCL` as I2C pins, `ADR` as the address-select pin, and says `PS0/PS1` should normally be left unconnected.
- On many JetPack 6 images, that J12 `I2C1` header appears in Linux as `/dev/i2c-7`. This repo defaults to bus `7`, but you can override it with `MP_IMU_I2C_BUS`.

Important:

- An accelerometer cannot measure steady-state vehicle speed by itself. The IMU alert in this repo is therefore a proxy for rapid acceleration / aggressive speeding behavior, not a GPS-grade speedometer.

## Audio Amp Wiring

Use these free `J12` header pins because the IMU is already using `1/3/5/6`.

| SparkFun TPA2005D1 pin | Jetson Orin Nano pin | Notes |
| --- | --- | --- |
| `VCC` | `Pin 2` (`5V`) | Better output headroom for the amp than 3.3V |
| `GND` | `Pin 9` (`GND`) | Common ground |
| `IN+` | `Pin 15` (`GPIO27 / PWM-capable`) | Audio input from the Jetson; use this as the signal input |
| `IN-` | `Pin 14` (`GND`) or any Jetson GND pin | Ground reference for the amp input |
| `SHDN` | `Pin 29` (`GPIO01`) | Software mute / enable control |
| `OUT+` / `SPK+` | Speaker wire 1 | Connect directly to one speaker terminal |
| `OUT-` / `SPK-` | Speaker wire 2 | Connect directly to the other speaker terminal |

Speaker notes:

- The speaker's two wires go only to the amp outputs.
- Do not connect either speaker wire to Jetson ground.
- If the speaker is labeled `+` and `-`, connect `+` to amp `OUT+` and `-` to amp `OUT-`. If it is unlabeled, either way will still work for alert tones.

Amp notes:

- The TPA2005D1 accepts a ground-referenced input, so `IN-` should be tied to `GND` while the Jetson drives `IN+`.
- `SHDN` is active low on the amplifier. This repo drives the Jetson GPIO high to enable the amp only while playing an alert.
- This path is intended for simple alert tones, not high-fidelity audio playback.

If you need to validate the amp path by itself:

```bash
PYTHONPATH=. ./venv/bin/python tests/audio_alert_debug.py
```

To drive the amp with randomized PWM noise instead of a fixed tone:

```bash
PYTHONPATH=. ./venv/bin/python tests/audio_alert_debug.py --noise-seconds 5 --noise-deviation 40
```

To probe the pins while holding the amp enabled:

```bash
PYTHONPATH=. ./venv/bin/python tests/audio_alert_debug.py --probe-pins --continuous-seconds 5 --frequency-hz 440
```

For a direct speaker test on the PWM pin, use:

```bash
MP_AUDIO_OUTPUT_MODE=carrier_pwm PYTHONPATH=. ./venv/bin/python tests/audio_alert_debug.py --board-pin 15 --continuous-seconds 5 --frequency-hz 1000 --keep-shdn-enabled
```

This keeps the amp enabled and drives a steady audible tone on BOARD 15 while the test runs.

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

## IMU Bring-Up

Recommended IMU settings:

```bash
export MP_IMU_ENABLED=1
export MP_IMU_I2C_BUS=7
export MP_IMU_ADDRESS=0x28
export MP_IMU_USE_LINEAR_ACCELERATION=1
export MP_IMU_AXIS=magnitude
export MP_IMU_SPEED_THRESHOLD_MPS2=2.5
export MP_IMU_SUSTAIN_SECONDS=0.5
export MP_IMU_ALERT_COOLDOWN_SECONDS=8.0
export MP_IMU_SMOOTHING_ALPHA=0.2
```

To debug the sensor by itself before running the full DMS stack:

```bash
./venv/bin/python imu_bno055_debug.py
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

### IMU speeding monitor

- `MP_IMU_ENABLED` (default: `0`)
- `MP_IMU_I2C_BUS` (default: `7`)
- `MP_IMU_ADDRESS` (default: `0x28`)
- `MP_IMU_POLL_HZ` (default: `20`)
- `MP_IMU_USE_LINEAR_ACCELERATION` (default: `1`)
- `MP_IMU_AXIS` (default: `magnitude`, allowed: `magnitude`, `x`, `y`, `z`)
- `MP_IMU_SPEED_THRESHOLD_MPS2` (default: `2.5`)
- `MP_IMU_SUSTAIN_SECONDS` (default: `0.5`)
- `MP_IMU_ALERT_COOLDOWN_SECONDS` (default: `8.0`)
- `MP_IMU_CLEAR_RATIO` (default: `0.75`)
- `MP_IMU_SMOOTHING_ALPHA` (default: `0.2`)
- `MP_IMU_USE_EXTERNAL_CRYSTAL` (default: `1`)
- `MP_IMU_DEBUG` (default: `0`)

### Audio alert output

- `MP_AUDIO_ENABLED` (default: `0`)
- `MP_AUDIO_TONE_PIN` (default: `15`)
- `MP_AUDIO_SHUTDOWN_PIN` (default: `29`)
- `MP_AUDIO_ALERT_CODES` (default: `drowsiness_detected,head_inattention_detected`)
- `MP_AUDIO_DEFAULT_FREQUENCY_HZ` (default: `880`)
- `MP_AUDIO_QUEUE_SIZE` (default: `8`)
- `MP_AUDIO_PREFER_PWM` (default: `1`)
- `MP_AUDIO_SHUTDOWN_ACTIVE_HIGH` (default: `1`)
- `MP_AUDIO_STARTUP_MUTED` (default: `1`)
- `MP_AUDIO_FORCE_GPIO` (default: `0`)
- `MP_AUDIO_OUTPUT_MODE` (default: `pdm_gpio`)
- `MP_AUDIO_PWM_CARRIER_HZ` (default: `25000`)
- `MP_AUDIO_PWM_STEP_HZ` (default: `1000`)

Required Jetson permissions:

```bash
sudo usermod -aG i2c,gpio $USER
```

Log out and log back in after running that command so both the IMU and the audio alert GPIOs are accessible without `sudo`.

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

`head_inattention_detected` and `speeding_detected` use the same envelope with corresponding `data` fields.

## Security Notes

- Keep broker addresses, credentials, and tokens out of source control.
- Use TLS (`MP_MQTT_TLS=1`) for cloud brokers.
- Scope broker ACLs by topic and client ID.
