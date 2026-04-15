import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import os
import time
import numpy as np

import sys
from pathlib import Path

# Local imports
from modules.jetson_alert_dispatcher import JetsonAlertDispatcher
from modules.module_audio_alert import AudioAlertConfig, AudioAlertNotifier
from modules.module_event_router import EventRouter
from modules.module_face_landmarker import (
    LEFT_EYE_EAR_INDICES,
    RIGHT_EYE_EAR_INDICES,
    calculate_ear,
    draw_landmarks_on_image,
    get_head_vertical_position,
)
from modules.module_env_init import env_bool, env_bool_first, env_int
from modules.module_gpu_preprocessor import CUDA_AVAILABLE, CUDA_INFO, GpuPreprocessor
from modules.module_imu_speed_monitor import IMUSpeedMonitor, IMUSpeedMonitorConfig
from modules.module_latest_frame_reader import LatestFrameReader, SynchronousFrameReader
from modules.module_model_downloader import download_model
from modules.module_web_socket import WebSocketBroadcaster

# Model download setup
MODEL_DIR = "model/facenet_vpruned_quantized_v2.0.1"
MODEL_PATH = os.path.join(MODEL_DIR, "face_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
download_model(MODEL_URL, MODEL_PATH)

# ── Video Parameters ──
try:
    VIDEO_SOURCE = int(os.environ.get("MP_VIDEO_SOURCE", "0"))
except ValueError:
    VIDEO_SOURCE = os.environ.get("MP_VIDEO_SOURCE")
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

# ── GPU / Benchmark Parameters ──
# Set MP_BENCHMARK=1 to run both CPU and GPU preprocessing every 100 frames
# and print a side-by-side comparison at the end.
BENCHMARK_MODE = env_bool("MP_BENCHMARK", False)
BENCHMARK_INTERVAL = env_int("MP_BENCHMARK_INTERVAL", 100)  # compare every N frames

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

ws_broadcaster = None
sinks = []
if WS_ENABLED:
    ws_broadcaster = WebSocketBroadcaster(host=WS_HOST, port=WS_PORT)
    if not ws_broadcaster.start():
        ws_broadcaster = None
    else:
        sinks.append(ws_broadcaster)

dispatcher = None
if MQTT_ENABLED:
    dispatcher = JetsonAlertDispatcher.from_env()
    if not dispatcher.connect():
        dispatcher = None

# ── BLE notifier (direct-to-driver alerts) ──
ble_notifier = None
if BLE_ENABLED:
    try:
        from ble.ble_notifier import BLENotifier
        ble_notifier = BLENotifier()
        ble_notifier.start()
    except Exception as exc:
        print(f"BLE disabled: {exc}")
        ble_notifier = None

sound_notifier = None
audio_config = AudioAlertConfig.from_env()
if audio_config.enabled:
    try:
        sound_notifier = AudioAlertNotifier(router=None, config=audio_config)
    except Exception:
        sound_notifier = None

router = EventRouter(
    source_id=EVENT_SOURCE_ID,
    producer=EVENT_PRODUCER,
    schema_version=EVENT_SCHEMA_VERSION,
    dispatcher=dispatcher,
    ble_notifier=ble_notifier,
    sound_notifier=sound_notifier,
    sinks=sinks,
)

if sound_notifier is not None:
    sound_notifier.router = router
    try:
        sound_notifier.start()
    except Exception as exc:
        router.emit_log(f"Audio alerts disabled: {exc}", level="error")
        sound_notifier = None
        router.sound_notifier = None

imu_monitor = None
imu_config = IMUSpeedMonitorConfig.from_env()
if imu_config.enabled:
    try:
        imu_monitor = IMUSpeedMonitor(router=router, config=imu_config)
        imu_monitor.start()
    except Exception as exc:
        router.emit_log(f"IMU disabled: {exc}", level="error")
        imu_monitor = None

if not sinks and dispatcher is None and ble_notifier is None:
    router.emit_log("Warning: no event sink enabled. Alerts will not be forwarded.")


cap = cv2.VideoCapture(VIDEO_SOURCE)

if not cap.isOpened():
    router.emit_log(f"Error: Could not open video source {VIDEO_SOURCE}", level="error")
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

router.emit_log(f"Video info: {width}x{height} @ {fps} FPS")
router.emit_log(
    f"Latency config: buffer_set_ok={buffer_set_ok} buffer_size={buffer_size_after} "
    f"capture_queue={CAPTURE_QUEUE_SIZE} display={DISPLAY_ENABLED} "
    f"save_output={SAVE_OUTPUT_VIDEO} target_fps={CAMERA_TARGET_FPS}"
)
router.emit_log(f"Using model: {MODEL_PATH}")

# ── GPU Preprocessor ──
# ── Force CUDA Preprocessing for V2 ──
gpu_preprocessor = GpuPreprocessor(use_gpu=CUDA_AVAILABLE)
cpu_preprocessor = GpuPreprocessor(use_gpu=False)

if CUDA_AVAILABLE:
    router.emit_log(
        f"CUDA ENABLED: {CUDA_INFO['device_count']} device(s) detected — "
        f"preprocessing will run on GPU"
    )
else:
    router.emit_log(
        "CUDA not available: preprocessing will run on CPU. "
        "For GPU acceleration, install OpenCV built with CUDA support."
    )

if BENCHMARK_MODE:
    router.emit_log(
        f"BENCHMARK MODE ON: comparing CPU vs {gpu_preprocessor.backend_label} "
        f"every {BENCHMARK_INTERVAL} frames"
    )

# Setup output videos
out = None
if SAVE_OUTPUT_VIDEO:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(OUTPUT_VIDEO_PATH, fourcc, int(fps), (width, height))
    if not out.isOpened():
        router.emit_log(f"Warning: failed to open output video '{OUTPUT_VIDEO_PATH}'. Disabling writer.")
        out = None

# Always record raw video for cross-model testing
recordings_dir = str(Path(__file__).parent / "recordings")
os.makedirs(recordings_dir, exist_ok=True)
raw_video_path = os.path.join(recordings_dir, f"session_{Path(__file__).stem}_{int(time.time())}.mp4")
raw_out = cv2.VideoWriter(raw_video_path, cv2.VideoWriter_fourcc(*"mp4v"), int(fps), (width, height))
router.emit_log(f"Recording RAW session video to {raw_video_path}")

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
if isinstance(VIDEO_SOURCE, str):
    router.emit_log("Using SynchronousFrameReader for video file analysis")
    frame_reader = SynchronousFrameReader(cap)
else:
    router.emit_log("Using LatestFrameReader for live camera feed")
    frame_reader = LatestFrameReader(cap, queue_size=CAPTURE_QUEUE_SIZE)
frame_reader.start()

try:
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        while True:
            timestamp_ms, frame = frame_reader.read(timeout=1.0)
            if frame is None:
                if frame_reader.stop_event.is_set():
                    router.emit_log("Frame reader stopped: camera stream ended.")
                    break
                continue

            frame_count += 1
            
            # Save raw unmodified frame to persistent storage
            if raw_out and raw_out.isOpened():
                raw_out.write(frame)

            # ── Preprocessing (GPU-accelerated when available) ──
            rgb_frame = gpu_preprocessor.bgr_to_rgb(frame)

            # Benchmark: also run CPU path on the same frame for comparison
            if BENCHMARK_MODE and frame_count % BENCHMARK_INTERVAL == 0:
                cpu_preprocessor.bgr_to_rgb(frame)

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=rgb_frame
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
                                router.emit_log(message, level="warning")
                                router.emit_alert(
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
                        router.emit_log(
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
                                    router.emit_log(message, level="warning")
                                    router.emit_alert(
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

            if frame_count % 10 == 0:
                lag_ms = max(0, int(time.time() * 1000) - timestamp_ms)
                pp_stats = gpu_preprocessor.stats()
                router.emit_log(
                    f"Processed {frame_count} frames | "
                    f"capture_read={frame_reader.frames_read} dropped={frame_reader.frames_dropped} "
                    f"lag_ms={lag_ms} preprocess={pp_stats['backend']} "
                    f"avg_pp_ms={pp_stats['avg_preprocess_ms']}"
                )
except KeyboardInterrupt:
    router.emit_log("Interrupted by user", level="warning")
finally:
    frame_reader.stop()
    cap.release()
    if out is not None:
        out.release()
    if raw_out is not None:
        raw_out.release()
    if DISPLAY_ENABLED:
        cv2.destroyAllWindows()
    if imu_monitor is not None:
        imu_monitor.stop()
    if sound_notifier is not None:
        sound_notifier.stop()
    if ws_broadcaster is not None:
        ws_broadcaster.stop()
    if dispatcher is not None:
        dispatcher.close()
    if ble_notifier is not None:
        ble_notifier.stop()

router.emit_log(f"\n{'='*50}")
router.emit_log(f"Processing complete! Total frames: {frame_count}")
router.emit_log(f"Total Blinks: {TOTAL_BLINKS}")
router.emit_log(f"Eye Closure Events: {DROWSY_EVENT_COUNT}")
router.emit_log(f"Head Inattention Events: {HEAD_INATTENTION_COUNT}")
if imu_monitor is not None:
    router.emit_log(f"IMU Speeding Events: {imu_monitor.event_count}")

# ── Preprocessing Performance Summary ──
gpu_stats = gpu_preprocessor.stats()
router.emit_log(f"\nPreprocessing backend: {gpu_stats['backend']}")
router.emit_log(
    f"  Total frames preprocessed: {gpu_stats['frames_processed']}  "
    f"Avg: {gpu_stats['avg_preprocess_ms']:.4f} ms/frame  "
    f"Total: {gpu_stats['total_preprocess_s']:.4f} s"
)

if BENCHMARK_MODE:
    cpu_stats = cpu_preprocessor.stats()
    router.emit_log(f"\n{'─'*50}")
    router.emit_log(f"  BENCHMARK RESULTS (CPU vs {gpu_stats['backend']})")
    router.emit_log(f"{'─'*50}")
    router.emit_log(
        f"  CPU:  {cpu_stats['avg_preprocess_ms']:.4f} ms/frame  "
        f"({cpu_stats['frames_processed']} samples)"
    )
    router.emit_log(
        f"  {gpu_stats['backend']}:  {gpu_stats['avg_preprocess_ms']:.4f} ms/frame  "
        f"({gpu_stats['frames_processed']} samples)"
    )
    if cpu_stats['avg_preprocess_ms'] > 0 and gpu_stats['avg_preprocess_ms'] > 0:
        speedup = cpu_stats['avg_preprocess_ms'] / gpu_stats['avg_preprocess_ms']
        router.emit_log(f"  Speedup: {speedup:.2f}x")
    router.emit_log(f"{'─'*50}")

router.emit_log(f"{'='*50}")
