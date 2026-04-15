"""
Benchmark test: Compare MediaPipe (CPU) vs TensorRT (GPU) Face Landmarker.
Run on the Jetson with:
  cd ~/Developer/mediapipe && source venv/bin/activate
  python3 test_trt_benchmark.py
"""
import cv2
import numpy as np
import time
import os

# ── Step 1: Test TensorRT Pipeline independently ──
print("=" * 60)
print("  TensorRT Face Landmarker Benchmark")
print("=" * 60)

# Check ONNX models exist
DET_ONNX = "../model/extracted_task/face_detector.onnx"
LAN_ONNX = "../model/extracted_task/face_landmarks_detector.onnx"

if not os.path.exists(DET_ONNX):
    print(f"ERROR: {DET_ONNX} not found. Run model conversion first.")
    exit(1)
if not os.path.exists(LAN_ONNX):
    print(f"ERROR: {LAN_ONNX} not found. Run model conversion first.")
    exit(1)

print(f"\nDetector model:  {DET_ONNX}")
print(f"Landmarker model: {LAN_ONNX}")

# ── Step 2: Initialize the TensorRT Landmarker ──
print("\n[1/4] Loading TensorRT engine (first run builds TRT cache, may take ~60s)...")
from module_tensorrt_landmarker import TensorRTLandmarker

trt_landmarker = TensorRTLandmarker(DET_ONNX, LAN_ONNX)

# ── Step 3: Load test frames ──
VIDEO = os.environ.get("TEST_VIDEO", "test_video.mp4")
if not os.path.exists(VIDEO):
    print(f"\nNo test video at '{VIDEO}', using camera for 5s...")
    cap = cv2.VideoCapture(0)
    NUM_FRAMES = 150
else:
    print(f"\n[2/4] Loading frames from {VIDEO}...")
    cap = cv2.VideoCapture(VIDEO)
    NUM_FRAMES = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

frames = []
while len(frames) < min(NUM_FRAMES, 300):  # cap at 300 frames
    ret, frame = cap.read()
    if not ret:
        break
    frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
cap.release()

print(f"  Loaded {len(frames)} frames at {frames[0].shape[1]}x{frames[0].shape[0]}")

if len(frames) == 0:
    print("ERROR: No frames loaded!")
    exit(1)

# ── Step 4: Warm up TensorRT (first inference triggers engine build) ──
print("\n[3/4] Warming up TensorRT engine (first inference is slow)...")

class FrameWrapper:
    """Minimal wrapper to mimic mp.Image.numpy_view()"""
    def __init__(self, arr):
        self._data = arr
    def numpy_view(self):
        return self._data

for i in range(min(5, len(frames))):
    trt_landmarker.detect_for_video(FrameWrapper(frames[i]), i * 33)

# Reset stats after warmup
trt_landmarker._det_times.clear()
trt_landmarker._lan_times.clear()
trt_landmarker._total_times.clear()

# ── Step 5: Benchmark TensorRT ──
print(f"\n[4/4] Benchmarking TensorRT on {len(frames)} frames...")
trt_face_count = 0
for i, frame in enumerate(frames):
    result = trt_landmarker.detect_for_video(FrameWrapper(frame), i * 33)
    if result.face_landmarks:
        trt_face_count += 1

trt_landmarker.print_stats()
print(f"  Faces detected: {trt_face_count}/{len(frames)}")

# ── Step 6: Validate landmark quality ──
print("\n" + "=" * 60)
print("  Landmark Quality Check")
print("=" * 60)

# Run on first frame with face and print key landmarks
for i, frame in enumerate(frames):
    result = trt_landmarker.detect_for_video(FrameWrapper(frame), 99999)
    if result.face_landmarks:
        lm = result.face_landmarks[0]
        print(f"\n  Frame {i}: {len(lm)} landmarks detected")
        print(f"  Nose tip (idx 1):      x={lm[1].x:.4f}  y={lm[1].y:.4f}")
        print(f"  Left eye (idx 33):     x={lm[33].x:.4f}  y={lm[33].y:.4f}")
        print(f"  Right eye (idx 263):   x={lm[263].x:.4f}  y={lm[263].y:.4f}")
        print(f"  Forehead (idx 10):     x={lm[10].x:.4f}  y={lm[10].y:.4f}")
        print(f"  Chin (idx 152):        x={lm[152].x:.4f}  y={lm[152].y:.4f}")

        # Sanity check: left eye should be to the left of right eye
        # (in image coords, left eye has smaller x for a frontal face)
        if lm[33].x < lm[263].x:
            print("  ✓ Eye positions look correct (left < right)")
        else:
            print("  ⚠ Eyes may be swapped — check the affine transform")

        # Sanity: forehead above chin
        if lm[10].y < lm[152].y:
            print("  ✓ Vertical ordering correct (forehead above chin)")
        else:
            print("  ⚠ Vertical ordering wrong — check coordinate mapping")

        # EAR test
        from module_face_landmarker import calculate_ear, LEFT_EYE_EAR_INDICES, RIGHT_EYE_EAR_INDICES
        left_ear = calculate_ear(lm, LEFT_EYE_EAR_INDICES, (frame.shape[0], frame.shape[1]))
        right_ear = calculate_ear(lm, RIGHT_EYE_EAR_INDICES, (frame.shape[0], frame.shape[1]))
        avg_ear = (left_ear + right_ear) / 2.0
        print(f"  EAR: left={left_ear:.4f}  right={right_ear:.4f}  avg={avg_ear:.4f}")
        if 0.15 < avg_ear < 0.45:
            print("  ✓ EAR is in a reasonable range for open eyes")
        else:
            print(f"  ⚠ EAR={avg_ear:.4f} seems unusual (expected 0.20-0.35 for open eyes)")

        break
else:
    print("  No faces found in any frame!")

print("\n" + "=" * 60)
print("  Done!")
print("=" * 60)
