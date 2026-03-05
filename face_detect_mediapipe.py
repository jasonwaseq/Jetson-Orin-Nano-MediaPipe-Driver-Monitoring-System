import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import asyncio
from datetime import datetime, timezone
import json
import math
import time
import os
import queue
import threading
import urllib.request
import uuid
import numpy as np
from jetson_alert_dispatcher import JetsonAlertDispatcher


def utc_timestamp():
    """Return an RFC3339 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def env_bool(name, default=False):
    """Parse boolean environment variables."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name, default):
    """Parse integer environment variables."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_first(names, default=None):
    """Return first non-empty environment variable from a list of names."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def env_int_first(names, default):
    """Parse first non-empty integer environment variable from names."""
    value = env_first(names)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool_first(names, default=False):
    """Parse first non-empty boolean environment variable from names."""
    value = env_first(names)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


class WebSocketBroadcaster:
    """Broadcast JSON messages to all connected websocket clients."""
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.clients = set()
        self.queue = queue.Queue()
        self.thread = None
        self.loop = None
        self._server = None
        self._running = threading.Event()
        self._enabled = False
        self._ws_mod = None
        self._startup_error = None

    def start(self):
        """Start websocket server on a background thread."""
        try:
            import websockets  # pylint: disable=import-outside-toplevel
            self._ws_mod = websockets
        except ImportError:
            print("WebSocket disabled: install dependency with 'pip install websockets'")
            return False

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._running.wait(timeout=3.0)
        if self._startup_error is not None:
            print(f"WebSocket startup failed: {self._startup_error}")
        return self._enabled

    def send(self, payload):
        """Queue a payload for broadcast."""
        if not self._enabled:
            return
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            pass

    def stop(self):
        """Stop websocket server and worker loop."""
        if not self._enabled:
            return
        self._enabled = False
        self.queue.put_nowait(None)
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=3.0)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
            self.loop.create_task(self._pump())
            self._enabled = True
        except Exception as exc:
            self._startup_error = exc
            self._enabled = False
            self._running.set()
            self.loop.close()
            return
        self._running.set()
        try:
            self.loop.run_forever()
        finally:
            self._enabled = False
            if self._server is not None:
                self._server.close()
                self.loop.run_until_complete(self._server.wait_closed())
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    async def _serve(self):
        async def handler(websocket):
            self.clients.add(websocket)
            try:
                async for _ in websocket:
                    pass
            except Exception:
                pass
            finally:
                self.clients.discard(websocket)

        self._server = await self._ws_mod.serve(handler, self.host, self.port)
        print(f"WebSocket server listening on ws://{self.host}:{self.port}")

    async def _pump(self):
        while True:
            item = await asyncio.to_thread(self.queue.get)
            if item is None:
                break
            if not self.clients:
                continue
            message = json.dumps(item)
            stale = []
            # Iterate over a snapshot; handlers may add/remove clients concurrently.
            for client in tuple(self.clients):
                try:
                    await client.send(message)
                except Exception:
                    stale.append(client)
            for client in stale:
                self.clients.discard(client)


class LatestFrameReader:
    """Read camera frames on a background thread and keep only the newest frame."""

    def __init__(self, cap, queue_size=1):
        self.cap = cap
        self.queue = queue.Queue(maxsize=max(1, queue_size))
        self.stop_event = threading.Event()
        self.thread = None
        self.frames_read = 0
        self.frames_dropped = 0
        self.read_failed = False

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            success, frame = self.cap.read()
            if not success:
                self.read_failed = True
                self.stop_event.set()
                break
            self.frames_read += 1
            item = (int(time.time() * 1000), frame)
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                    self.frames_dropped += 1
                except queue.Empty:
                    pass
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.frames_dropped += 1

    def read(self, timeout=1.0):
        """Return (timestamp_ms, frame) or (None, None) when no frame is available."""
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None, None

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)


# Model download setup
MODEL_DIR = "../model/facenet_vpruned_quantized_v2.0.1"
MODEL_PATH = os.path.join(MODEL_DIR, "face_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

def download_model(url, path):
    """Download model if not exists"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        print(f"Downloading {os.path.basename(path)} (~5MB)...")
        urllib.request.urlretrieve(url, path)
        print("Download complete!")
    else:
        print(f"Model found at {path}")

# Download/check model
download_model(MODEL_URL, MODEL_PATH)

VIDEO_SOURCE = 0
CAMERA_BUFFER_SIZE = max(1, env_int("MP_CAMERA_BUFFER_SIZE", 1))
CAMERA_TARGET_FPS = env_int("MP_CAMERA_TARGET_FPS", 30)
CAPTURE_QUEUE_SIZE = max(1, env_int("MP_CAPTURE_QUEUE_SIZE", 1))
DISPLAY_ENABLED = env_bool("MP_DISPLAY_ENABLED", True)
SAVE_OUTPUT_VIDEO = env_bool("MP_SAVE_OUTPUT_VIDEO", False)
OUTPUT_VIDEO_PATH = os.getenv("MP_OUTPUT_VIDEO_PATH", "output_with_landmarks.mp4")

# ── Event Routing Parameters ──
EVENT_SOURCE_ID = os.getenv("MP_SOURCE_ID", "jetson-01")
EVENT_PRODUCER = os.getenv("MP_EVENT_PRODUCER", "mediapipe-driver-monitor")
EVENT_SCHEMA_VERSION = os.getenv("MP_EVENT_SCHEMA_VERSION", "1.0")

# ── WebSocket Output Parameters (optional local debug only) ──
WS_ENABLED = env_bool("MP_WS_ENABLED", False)
WS_HOST = os.getenv("MP_WS_HOST", "0.0.0.0")
WS_PORT = env_int("MP_WS_PORT", 8765)

# ── MQTT Uplink Parameters (primary integration path) ──
MQTT_ENABLED = env_bool_first(["MP_MQTT_ENABLED", "MP_QTT_ENABLED", "MPMQTT_ENABLED", "MPQTT_ENABLED"], True)

# ── EAR (Eye Aspect Ratio) Parameters ──
EAR_THRESHOLD = 0.21          # Below this = eyes closed (lowered for better sensitivity)
EAR_CONSEC_FRAMES = 2         # Consecutive closed frames to register a blink

# ── Drowsiness (Prolonged Eye Closure) Parameters ──
DROWSY_TIME_THRESHOLD = 1.5   # Seconds of continuous eye closure = drowsy event

# ── Head Pose (Attention) Parameters ──
# We build a baseline of the driver's normal head position over the first few seconds.
# If the head's vertical position deviates from baseline for too long, flag inattention.
HEAD_BASELINE_WINDOW = 90     # Frames to build initial baseline (~3s at 30fps)
HEAD_DEVIATION_THRESHOLD = 0.06  # Normalized deviation from baseline to flag
HEAD_INATTEN_TIME_THRESH = 2.0   # Seconds of sustained deviation = inattention event
HEAD_SMOOTHING_ALPHA = 0.3    # EMA smoothing for head position (0-1, lower = smoother)

# ── State Variables ──
TOTAL_BLINKS = 0
BLINK_COUNTER = 0
START_TIME = time.time()

# Drowsiness state
EYES_CLOSED_START = None
DROWSY_ALERT_ACTIVE = False
DROWSY_EVENT_COUNT = 0

# Head attention state
head_baseline_samples = []        # samples during calibration
head_baseline_y = None            # calibrated baseline (normalized y of nose tip)
head_smoothed_y = None            # EMA-smoothed current head y
head_deviated_start = None        # when head first deviated
HEAD_INATTENTION_ACTIVE = False
HEAD_INATTENTION_COUNT = 0

event_sinks = []
event_sequence = 0
event_sequence_lock = threading.Lock()

ws_broadcaster = None
if WS_ENABLED:
    ws_broadcaster = WebSocketBroadcaster(host=WS_HOST, port=WS_PORT)
    if not ws_broadcaster.start():
        ws_broadcaster = None
    else:
        event_sinks.append(ws_broadcaster)

dispatcher = None
if MQTT_ENABLED:
    dispatcher = JetsonAlertDispatcher.from_env()
    if not dispatcher.connect():
        dispatcher = None

if not event_sinks and dispatcher is None:
    print("Warning: no event sink enabled. Alerts will not be forwarded.")


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


def emit_alert(code, message, severity="warning", **data):
    """Emit alert payload to all configured event sinks."""
    payload = {"code": code, "message": message, "severity": severity}
    if data:
        payload["data"] = data
    emit_event("alert", **payload)
    if dispatcher is not None:
        metadata = {"code": code, "severity": severity}
        metadata.update(data)
        ok = dispatcher.publish_alert(
            level=severity_to_level(severity),
            message=message,
            metadata=metadata,
        )
        emit_log(f"MQTT publish ok={ok} code={code} message={message}")


cap = cv2.VideoCapture(VIDEO_SOURCE)

if not cap.isOpened():
    emit_log(f"Error: Could not open video source {VIDEO_SOURCE}", level="error")
    exit()

buffer_set_ok = cap.set(cv2.CAP_PROP_BUFFERSIZE, CAMERA_BUFFER_SIZE)
if CAMERA_TARGET_FPS > 0:
    cap.set(cv2.CAP_PROP_FPS, CAMERA_TARGET_FPS)

# Get video properties
fps = cap.get(cv2.CAP_PROP_FPS)
if fps == 0:
    fps = 30
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
buffer_size_after = cap.get(cv2.CAP_PROP_BUFFERSIZE)

emit_log(f"Video info: {width}x{height} @ {fps} FPS")
emit_log(
    f"Latency config: buffer_set_ok={buffer_set_ok} buffer_size={buffer_size_after} "
    f"capture_queue={CAPTURE_QUEUE_SIZE} display={DISPLAY_ENABLED} "
    f"save_output={SAVE_OUTPUT_VIDEO} target_fps={CAMERA_TARGET_FPS}"
)
emit_log(f"Using model: {MODEL_PATH}")

# Setup output video
out = None
if SAVE_OUTPUT_VIDEO:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(OUTPUT_VIDEO_PATH, fourcc, int(fps), (width, height))
    if not out.isOpened():
        emit_log(f"Warning: failed to open output video '{OUTPUT_VIDEO_PATH}'. Disabling writer.")
        out = None

# Create FaceLandmarker options
base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    num_faces=1,
    min_face_detection_confidence=0.5,
    min_face_presence_confidence=0.5,
    min_tracking_confidence=0.5
)


def calculate_ear(face_landmarks, indices, image_shape):
    """
    Calculate Eye Aspect Ratio (EAR).
    Uses 6 landmark points: 2 corner + 4 vertical.
    """
    def get_coords(idx):
        return (face_landmarks[idx].x * image_shape[1],
                face_landmarks[idx].y * image_shape[0])

    p1 = get_coords(indices[0])  # left corner
    p2 = get_coords(indices[1])  # top-1
    p3 = get_coords(indices[2])  # top-2
    p4 = get_coords(indices[3])  # right corner
    p5 = get_coords(indices[4])  # bottom-2
    p6 = get_coords(indices[5])  # bottom-1

    v1 = math.hypot(p2[0] - p6[0], p2[1] - p6[1])
    v2 = math.hypot(p3[0] - p5[0], p3[1] - p5[1])
    h  = math.hypot(p1[0] - p4[0], p1[1] - p4[1])

    if h == 0:
        return 0.0
    return (v1 + v2) / (2.0 * h)


def get_head_vertical_position(face_landmarks):
    """
    Get a normalized vertical position of the head using the nose tip (landmark 1).
    Returns the raw normalized y coordinate (0=top, 1=bottom).
    We use the nose tip relative to the face bounding box to be scale-invariant.
    """
    # Nose tip
    nose_y = face_landmarks[1].y

    # Use forehead (10) and chin (152) to normalize within the face
    forehead_y = face_landmarks[10].y
    chin_y = face_landmarks[152].y
    face_height = abs(chin_y - forehead_y)

    if face_height < 0.001:
        return nose_y  # fallback

    # Nose position relative to forehead-chin range
    # 0 = at forehead level, 1 = at chin level
    # When head tilts down, nose_y increases relative to forehead
    relative_y = (nose_y - forehead_y) / face_height
    return relative_y


def draw_landmarks_on_image(image, detection_result):
    """Draw face landmarks on the image"""
    if not detection_result.face_landmarks:
        return image

    annotated_image = image.copy()

    for face_landmarks in detection_result.face_landmarks:
        for landmark in face_landmarks:
            x = int(landmark.x * image.shape[1])
            y = int(landmark.y * image.shape[0])
            cv2.circle(annotated_image, (x, y), 1, (0, 255, 0), -1)

        left_eye_indices = [33, 160, 158, 133, 153, 144, 33]
        right_eye_indices = [362, 385, 387, 263, 373, 380, 362]

        for eye_indices in [left_eye_indices, right_eye_indices]:
            for i in range(len(eye_indices) - 1):
                pt1 = face_landmarks[eye_indices[i]]
                pt2 = face_landmarks[eye_indices[i + 1]]
                x1 = int(pt1.x * image.shape[1])
                y1 = int(pt1.y * image.shape[0])
                x2 = int(pt2.x * image.shape[1])
                y2 = int(pt2.y * image.shape[0])
                cv2.line(annotated_image, (x1, y1), (x2, y2), (255, 0, 0), 1)

    return annotated_image


# MediaPipe landmark indices for EAR
LEFT_EYE_EAR_INDICES  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_EAR_INDICES = [362, 385, 387, 263, 373, 380]

# ── Main Loop ──
frame_count = 0
frame_reader = LatestFrameReader(cap, queue_size=CAPTURE_QUEUE_SIZE)
frame_reader.start()

try:
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        while True:
            timestamp_ms, frame = frame_reader.read(timeout=1.0)
            if frame is None:
                if frame_reader.stop_event.is_set():
                    emit_log("Frame reader stopped: camera stream ended.")
                    break
                continue

            frame_count += 1

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            )

            detection_result = landmarker.detect_for_video(mp_image, timestamp_ms)

            annotated_frame = draw_landmarks_on_image(frame, detection_result)

            current_time = time.time()

            if detection_result.face_landmarks:
                face_landmarks = detection_result.face_landmarks[0]

                # ── 1. EAR + Blink + Drowsiness ──
                left_ear = calculate_ear(face_landmarks, LEFT_EYE_EAR_INDICES, frame.shape)
                right_ear = calculate_ear(face_landmarks, RIGHT_EYE_EAR_INDICES, frame.shape)
                ear = (left_ear + right_ear) / 2.0

                if ear < EAR_THRESHOLD:
                    BLINK_COUNTER += 1

                    # Drowsiness: track how long eyes have been closed
                    if EYES_CLOSED_START is None:
                        EYES_CLOSED_START = current_time
                    else:
                        closed_duration = current_time - EYES_CLOSED_START
                        if closed_duration >= DROWSY_TIME_THRESHOLD:
                            if not DROWSY_ALERT_ACTIVE:
                                DROWSY_EVENT_COUNT += 1
                                message = (f"DROWSINESS DETECTED! Event #{DROWSY_EVENT_COUNT}"
                                           f" (eyes closed {closed_duration:.1f}s)")
                                emit_log(message, level="warning")
                                emit_alert(
                                    "drowsiness_detected",
                                    message,
                                    severity="critical",
                                    event_count=DROWSY_EVENT_COUNT,
                                    closed_duration_sec=round(closed_duration, 3),
                                    ear=round(ear, 3),
                                    blink_ms=int(closed_duration * 1000),
                                )
                            DROWSY_ALERT_ACTIVE = True
                else:
                    # Eyes open — check if we just finished a blink
                    if BLINK_COUNTER >= EAR_CONSEC_FRAMES:
                        TOTAL_BLINKS += 1
                    BLINK_COUNTER = 0
                    EYES_CLOSED_START = None
                    DROWSY_ALERT_ACTIVE = False

                # ── 2. Head Vertical Attention ──
                head_y = get_head_vertical_position(face_landmarks)

                # Smooth the measurement with EMA
                if head_smoothed_y is None:
                    head_smoothed_y = head_y
                else:
                    head_smoothed_y = (HEAD_SMOOTHING_ALPHA * head_y
                                       + (1 - HEAD_SMOOTHING_ALPHA) * head_smoothed_y)

                # Build baseline during first N frames
                if len(head_baseline_samples) < HEAD_BASELINE_WINDOW:
                    head_baseline_samples.append(head_smoothed_y)
                    if len(head_baseline_samples) == HEAD_BASELINE_WINDOW:
                        head_baseline_y = np.mean(head_baseline_samples)
                        emit_log(
                            f"Head baseline calibrated: {head_baseline_y:.4f}"
                            f" (from {HEAD_BASELINE_WINDOW} frames)"
                        )

                # Check deviation from baseline
                if head_baseline_y is not None:
                    deviation = abs(head_smoothed_y - head_baseline_y)

                    if deviation > HEAD_DEVIATION_THRESHOLD:
                        if head_deviated_start is None:
                            head_deviated_start = current_time
                        else:
                            deviated_duration = current_time - head_deviated_start
                            if deviated_duration >= HEAD_INATTEN_TIME_THRESH:
                                if not HEAD_INATTENTION_ACTIVE:
                                    HEAD_INATTENTION_COUNT += 1
                                    message = (
                                        f"HEAD INATTENTION DETECTED! Event #{HEAD_INATTENTION_COUNT}"
                                        f" (deviated {deviated_duration:.1f}s)"
                                    )
                                    emit_log(message, level="warning")
                                    emit_alert(
                                        "head_inattention_detected",
                                        message,
                                        severity="high",
                                        event_count=HEAD_INATTENTION_COUNT,
                                        deviation=round(deviation, 4),
                                        deviated_duration_sec=round(deviated_duration, 3),
                                    )
                                HEAD_INATTENTION_ACTIVE = True
                    else:
                        head_deviated_start = None
                        HEAD_INATTENTION_ACTIVE = False

                # ── 3. Display Stats (all green, top of frame) ──
                TEXT_COLOR = (0, 255, 0)
                TEXT_COLOR_1 = (255, 255, 0)
                y_pos = 30

                cv2.putText(annotated_frame, f"Blinks: {TOTAL_BLINKS}", (10, y_pos),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT_COLOR, 2)
                y_pos += 30

                elapsed = current_time - START_TIME
                bpm = 0.0
                if elapsed > 0:
                    bpm = (TOTAL_BLINKS / elapsed) * 60
                    cv2.putText(annotated_frame, f"Blink Freq: {bpm:.1f} BPM", (10, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT_COLOR, 2)
                y_pos += 30

                cv2.putText(annotated_frame, f"Eye Closure Events: {DROWSY_EVENT_COUNT}", (10, y_pos),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT_COLOR_1, 2)
                y_pos += 30

                cv2.putText(annotated_frame, f"Head Inattention Events: {HEAD_INATTENTION_COUNT}", (10, y_pos),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT_COLOR_1, 2)
                y_pos += 30

                # Calibration indicator
                if head_baseline_y is None:
                    progress = len(head_baseline_samples) / HEAD_BASELINE_WINDOW * 100
                    cv2.putText(annotated_frame, f"Calibrating head... {progress:.0f}%", (10, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

            else:
                cv2.putText(annotated_frame, "No Face Detected - Highly Likely Driver Is Asleep", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            if out is not None:
                out.write(annotated_frame)

            if DISPLAY_ENABLED:
                cv2.imshow('Driver Drowsiness Monitor', annotated_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            if frame_count % 100 == 0:
                lag_ms = max(0, int(time.time() * 1000) - timestamp_ms)
                emit_log(
                    f"Processed {frame_count} frames | "
                    f"capture_read={frame_reader.frames_read} dropped={frame_reader.frames_dropped} "
                    f"lag_ms={lag_ms}"
                )
except KeyboardInterrupt:
    emit_log("Interrupted by user", level="warning")
finally:
    frame_reader.stop()
    cap.release()
    if out is not None:
        out.release()
    if DISPLAY_ENABLED:
        cv2.destroyAllWindows()
    if ws_broadcaster is not None:
        ws_broadcaster.stop()
    if dispatcher is not None:
        dispatcher.close()

emit_log(f"\n{'='*50}")
emit_log(f"Processing complete! Total frames: {frame_count}")
emit_log(f"Total Blinks: {TOTAL_BLINKS}")
emit_log(f"Eye Closure Events: {DROWSY_EVENT_COUNT}")
emit_log(f"Head Inattention Events: {HEAD_INATTENTION_COUNT}")
emit_log(f"{'='*50}")
