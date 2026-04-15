"""
Comparison script to check accuracy difference between:
1. MediaPipe (Standard CPU)
2. TensorRT (Custom GPU Pipeline)
"""
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import time
import os
from modules.module_face_landmarker import calculate_ear, LEFT_EYE_EAR_INDICES, RIGHT_EYE_EAR_INDICES
from modules.module_tensorrt_landmarker import TensorRTLandmarker

# Path to the test video
VIDEO_PATH = "recordings/all_test_1775785006.mp4"
MODEL_PATH = "model/facenet_vpruned_quantized_v2.0.1/face_landmarker.task"
DET_ONNX = "model/extracted_task/face_detector.onnx"
LAN_ONNX = "model/extracted_task/face_landmarks_detector.onnx"

def run_mediapipe_inference(video_path):
    print("Running MediaPipe Inference...")
    base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1
    )
    
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    results = []
    
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret: break
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            
            timestamp_ms = int(frame_idx * 1000 / fps)
            detection_result = landmarker.detect_for_video(mp_image, timestamp_ms)
            
            ear = 0.0
            if detection_result.face_landmarks:
                face_landmarks = detection_result.face_landmarks[0]
                left_ear = calculate_ear(face_landmarks, LEFT_EYE_EAR_INDICES, frame.shape)
                right_ear = calculate_ear(face_landmarks, RIGHT_EYE_EAR_INDICES, frame.shape)
                ear = (left_ear + right_ear) / 2.0
            
            results.append(ear)
            frame_idx += 1
    cap.release()
    return results

def run_tensorrt_inference(video_path):
    print("Running TensorRT Inference...")
    trt = TensorRTLandmarker(DET_ONNX, LAN_ONNX)
    
    cap = cv2.VideoCapture(video_path)
    results = []
    
    frame_idx = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        class MockImage:
            def __init__(self, data): self.data = data
            def numpy_view(self): return self.data
            
        timestamp_ms = frame_idx * 33 # Approximate
        detection_result = trt.detect_for_video(MockImage(rgb_frame), timestamp_ms)
        
        ear = 0.0
        if detection_result.face_landmarks:
            face_landmarks = detection_result.face_landmarks[0]
            left_ear = calculate_ear(face_landmarks, LEFT_EYE_EAR_INDICES, frame.shape)
            right_ear = calculate_ear(face_landmarks, RIGHT_EYE_EAR_INDICES, frame.shape)
            ear = (left_ear + right_ear) / 2.0
            
        results.append(ear)
        frame_idx += 1
    cap.release()
    return results

if __name__ == "__main__":
    if not os.path.exists(VIDEO_PATH):
        print(f"Video not found: {VIDEO_PATH}")
        exit(1)
        
    mp_ears = run_mediapipe_inference(VIDEO_PATH)
    trt_ears = run_tensorrt_inference(VIDEO_PATH)
    
    # Compare
    length = min(len(mp_ears), len(trt_ears))
    mp_ears = np.array(mp_ears[:length])
    trt_ears = np.array(trt_ears[:length])
    
    # Filter out zeros (where face was not detected)
    mask = (mp_ears > 0) & (trt_ears > 0)
    
    if np.any(mask):
        diff = np.abs(mp_ears[mask] - trt_ears[mask])
        print("\n" + "="*40)
        print("ACCURACY COMPARISON (EAR)")
        print("="*40)
        print(f"Average MediaPipe EAR: {np.mean(mp_ears[mask]):.4f}")
        print(f"Average TensorRT EAR:  {np.mean(trt_ears[mask]):.4f}")
        print(f"Mean Absolute Error:   {np.mean(diff):.4f}")
        print(f"Max Absolute Error:    {np.max(diff):.4f}")
        print(f"Correlation:           {np.corrcoef(mp_ears[mask], trt_ears[mask])[0,1]:.4f}")
        print(f"Detections Rate:       MP={np.mean(mp_ears>0)*100:.1f}%, TRT={np.mean(trt_ears>0)*100:.1f}%")
        print("="*40)
    else:
        print("No overlapping detections found for comparison.")
