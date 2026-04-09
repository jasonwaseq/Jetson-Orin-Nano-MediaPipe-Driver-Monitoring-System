import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import os
import numpy as np
from module_model_downloader import download_model

MODEL_DIR = "../model/facenet_vpruned_quantized_v2.0.1"
MODEL_PATH = os.path.join(MODEL_DIR, "face_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
download_model(MODEL_URL, MODEL_PATH)

# Create a dummy frame (or use camera for 1 frame)
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
cap.release()
if not ret:
    print("No camera, using dummy frame")
    frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

print("Frame size:", frame.shape)

# Profile CPU delegate
base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    num_faces=1,
)

with vision.FaceLandmarker.create_from_options(options) as landmarker:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    # Warmup
    for i in range(10):
        landmarker.detect_for_video(mp_image, i * 33)

    # Benchmark CPU inference
    times = []
    for i in range(100):
        t0 = time.time()
        landmarker.detect_for_video(mp_image, (i + 10) * 33)
        times.append((time.time() - t0) * 1000)

    avg = np.mean(times)
    p50 = np.percentile(times, 50)
    p95 = np.percentile(times, 95)
    print()
    print("=== CPU Delegate (current) ===")
    print("  Avg: %.2f ms/frame" % avg)
    print("  P50: %.2f ms" % p50)
    print("  P95: %.2f ms" % p95)

# Profile GPU delegate
try:
    base_options_gpu = python.BaseOptions(
        model_asset_path=MODEL_PATH,
        delegate=python.BaseOptions.Delegate.GPU
    )
    options_gpu = vision.FaceLandmarkerOptions(
        base_options=base_options_gpu,
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
    )

    with vision.FaceLandmarker.create_from_options(options_gpu) as landmarker_gpu:
        # Warmup
        for i in range(10):
            landmarker_gpu.detect_for_video(mp_image, i * 33)

        # Benchmark GPU inference
        times_gpu = []
        for i in range(100):
            t0 = time.time()
            landmarker_gpu.detect_for_video(mp_image, (i + 10) * 33)
            times_gpu.append((time.time() - t0) * 1000)

        avg_g = np.mean(times_gpu)
        p50_g = np.percentile(times_gpu, 50)
        p95_g = np.percentile(times_gpu, 95)
        print()
        print("=== GPU Delegate ===")
        print("  Avg: %.2f ms/frame" % avg_g)
        print("  P50: %.2f ms" % p50_g)
        print("  P95: %.2f ms" % p95_g)
        print()
        print("Speedup: %.2fx" % (avg / avg_g))

except Exception as e:
    print()
    print("GPU delegate failed:", e)
    print("(This is expected if MediaPipe GPU delegate is not supported on this platform)")
