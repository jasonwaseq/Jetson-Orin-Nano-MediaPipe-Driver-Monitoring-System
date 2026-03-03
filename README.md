# MediaPipe Driver Alert Stream

This project runs face-based driver monitoring with MediaPipe and exposes **only alert events** over a WebSocket for a Flutter mobile client.

MediaPipe Integration: @J8Soham

## What It Detects

- Eye-closure (drowsiness) events
- Head-inattention events

When an event is detected, the script sends an `alert` JSON payload to connected WebSocket clients.

## Current WebSocket Behavior

The WebSocket server sends only:

- `alert` with `code: "drowsiness_detected"`
- `alert` with `code: "head_inattention_detected"`

It does **not** stream processed frames.

## Project Files

- `face_detect_mediapipe.py`: main detection + WebSocket broadcaster

## Requirements

- Python 3.10+
- Webcam (or update `VIDEO_SOURCE` in the script)
- Python packages used by the script:
  - `mediapipe`
  - `opencv-python`
  - `numpy`
  - `websockets`

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install mediapipe opencv-python numpy websockets
```

## Run

```bash
./venv/bin/python face_detect_mediapipe.py
```

## WebSocket Configuration

The script reads these environment variables:

- `MP_WS_ENABLED` (default: `1`)
- `MP_WS_HOST` (default: `0.0.0.0`)
- `MP_WS_PORT` (default: `8765`)

Example:

```bash
export MP_WS_ENABLED=1
export MP_WS_HOST=0.0.0.0
export MP_WS_PORT=8765
./venv/bin/python face_detect_mediapipe.py
```

Flutter app URL format:

```text
ws://<JETSON_OR_TAILSCALE_IP>:8765
```

Use your own runtime IP address; do not hardcode private network details into source files.

## Alert Message Shape

Example payload:

```json
{
  "type": "alert",
  "timestamp": "2026-03-03T20:00:00.000000+00:00",
  "code": "drowsiness_detected",
  "message": "DROWSINESS DETECTED! Event #3 (eyes closed 1.6s)",
  "severity": "critical",
  "data": {
    "event_count": 3,
    "closed_duration_sec": 1.634
  }
}
```

`head_inattention_detected` uses the same envelope with relevant fields in `data`.

## Security Notes

- Keep network addresses, tokens, and credentials out of source control.
- Use environment variables or local untracked config for deployment-specific values.
- Review logs before sharing externally.
