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

# Local imports
from jetson_alert_dispatcher import JetsonAlertDispatcher
from module_web_socket import *
from module_model_downloader import *
from module_face_landmarker import *
from module_env_init import *
from module_latest_frame_reader import *
from module_ble_emit import *

# Model download setup
MODEL_DIR = "../model/facenet_vpruned_quantized_v2.0.1"
MODEL_PATH = os.path.join(MODEL_DIR, "face_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
download_model(MODEL_URL, MODEL_PATH)

# ── Video Parameters ──
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

# ── BLE Direct-to-Driver Parameters ──
BLE_ENABLED = env_bool("MP_BLE_ENABLED", True)

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

# ── BLE notifier (direct-to-driver alerts) ──
ble_notifier = None
if BLE_ENABLED:
    try:
        from ble_notifier import BLENotifier
        ble_notifier = BLENotifier()
        ble_notifier.start()
    except Exception as e:
        print(f"BLE disabled: {e}")
        ble_notifier = None

if not event_sinks and dispatcher is None and ble_notifier is None:
    print("Warning: no event sink enabled. Alerts will not be forwarded.")


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
                                    dispatcher,
                                    ble_notifier,
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
                                        dispatcher,
                                        ble_notifier,
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
    if ble_notifier is not None:
        ble_notifier.stop()

emit_log(f"\n{'='*50}")
emit_log(f"Processing complete! Total frames: {frame_count}")
emit_log(f"Total Blinks: {TOTAL_BLINKS}")
emit_log(f"Eye Closure Events: {DROWSY_EVENT_COUNT}")
emit_log(f"Head Inattention Events: {HEAD_INATTENTION_COUNT}")
emit_log(f"{'='*50}")
