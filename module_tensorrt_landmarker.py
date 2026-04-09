"""
Custom Face Landmarker Engine using ONNX Runtime with TensorRT/CUDA Acceleration.
Replaces the MediaPipe Python wrapper to fully leverage the Jetson GPU.

Pipeline:
  1. Face Detector (BlazeFace SSD, 128x128) → bounding box
  2. Affine crop & align face region → 256x256
  3. Face Landmarks Detector (FaceMesh, 256x256) → 478 landmarks
  4. Reverse-project landmarks to original image coordinates

Requires:
  - onnxruntime-gpu (with TensorRT execution provider)
  - face_detector.onnx and face_landmarks_detector.onnx
"""

import numpy as np
import cv2
import time
import os

# ── Try to import onnxruntime; graceful fallback ──
try:
    import onnxruntime as ort
    ORT_AVAILABLE = True
except ImportError:
    ORT_AVAILABLE = False


# ═══════════════════════════════════════════════════════════
#  SSD Anchor Generation (BlazeFace)
# ═══════════════════════════════════════════════════════════

def _calculate_scale(min_scale, max_scale, stride_index, num_strides):
    """Calculate the anchor scale for a given layer."""
    if num_strides == 1:
        return (min_scale + max_scale) * 0.5
    return min_scale + (max_scale - min_scale) * stride_index / (num_strides - 1.0)


def generate_ssd_anchors():
    """
    Generate the 896 SSD anchors for the BlazeFace short-range detector (128×128).
    This replicates MediaPipe's SsdAnchorsCalculator with the exact same config.
    Returns: np.ndarray of shape (896, 4) as [x_center, y_center, w, h] in [0,1].
    """
    # Config from MediaPipe's face_detection_short_range.pbtxt
    strides = [8, 16, 16, 16]
    min_scale = 0.1484375
    max_scale = 0.75
    input_size = 128
    anchor_offset = 0.5
    aspect_ratios = [1.0]
    interpolated_scale_ar = 1.0

    anchors = []
    layer_id = 0

    while layer_id < len(strides):
        anchor_height = []
        anchor_width = []
        ar_list = []
        scales = []

        # Gather all layers that share the same stride
        last_same = layer_id
        while last_same < len(strides) and strides[last_same] == strides[layer_id]:
            scale = _calculate_scale(min_scale, max_scale, last_same, len(strides))

            for ar in aspect_ratios:
                ar_list.append(ar)
                scales.append(scale)

            if interpolated_scale_ar > 0:
                if last_same + 1 < len(strides):
                    scale_next = _calculate_scale(min_scale, max_scale, last_same + 1, len(strides))
                else:
                    scale_next = 1.0
                scales.append(np.sqrt(scale * scale_next))
                ar_list.append(interpolated_scale_ar)

            last_same += 1

        for i in range(len(ar_list)):
            ratio_sqrt = np.sqrt(ar_list[i])
            anchor_height.append(scales[i] / ratio_sqrt)
            anchor_width.append(scales[i] * ratio_sqrt)

        stride = strides[layer_id]
        feat_h = input_size // stride
        feat_w = input_size // stride

        for y in range(feat_h):
            for x in range(feat_w):
                for k in range(len(anchor_height)):
                    cx = (x + anchor_offset) / feat_w
                    cy = (y + anchor_offset) / feat_h
                    anchors.append([cx, cy, anchor_width[k], anchor_height[k]])

        layer_id = last_same

    return np.array(anchors, dtype=np.float32)


# ═══════════════════════════════════════════════════════════
#  Non-Maximum Suppression (Weighted)
# ═══════════════════════════════════════════════════════════

def _nms_weighted(boxes, scores, keypoints=None, iou_thresh=0.3, score_thresh=0.5):
    """
    Weighted Non-Maximum Suppression.
    BlazeFace uses weighted NMS where overlapping detections are blended
    (weighted by score) rather than discarded.

    boxes:  (N, 4) as [x1, y1, x2, y2]
    scores: (N,)
    Returns: list of (blended_box, blended_score, blended_keypoints) — we pass
             keypoints through the same path.
    """
    if len(scores) == 0:
        return []

    # Filter by score threshold
    mask = scores > score_thresh
    boxes = boxes[mask]
    filtered_kps = keypoints[mask] if keypoints is not None else None
    scores = scores[mask]

    if len(scores) == 0:
        return []

    # Sort by score descending
    order = np.argsort(-scores)
    boxes = boxes[order]
    scores = scores[order]

    keep = []
    used = np.zeros(len(boxes), dtype=bool)

    for i in range(len(boxes)):
        if used[i]:
            continue

        # Find all boxes overlapping with box i
        overlapping_indices = [i]
        for j in range(i + 1, len(boxes)):
            if used[j]:
                continue
            iou = _compute_iou(boxes[i], boxes[j])
            if iou > iou_thresh:
                overlapping_indices.append(j)
                used[j] = True

        # Weighted blend
        total_score = sum(scores[k] for k in overlapping_indices)
        blended_box = np.zeros(4, dtype=np.float32)
        for k in overlapping_indices:
            wt = scores[k] / total_score
            blended_box += wt * boxes[k]

        # Use keypoints from highest-score detection in this cluster
        best_kp = filtered_kps[overlapping_indices[0]] if filtered_kps is not None else None
        keep.append((blended_box, scores[i], best_kp))
        used[i] = True

    return keep


def _compute_iou(box_a, box_b):
    """Compute IoU between two [x1, y1, x2, y2] boxes."""
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - intersection

    if union <= 0:
        return 0.0
    return intersection / union


# ═══════════════════════════════════════════════════════════
#  Affine Transformation (Face Crop & Align)
# ═══════════════════════════════════════════════════════════

def _get_rotation_matrix(center, angle_rad, scale):
    """Get a 2x3 affine rotation matrix around center, with scaling."""
    cos_a = np.cos(angle_rad) * scale
    sin_a = np.sin(angle_rad) * scale
    cx, cy = center
    M = np.array([
        [cos_a, -sin_a, cx - cx * cos_a + cy * sin_a],
        [sin_a,  cos_a, cy - cx * sin_a - cy * cos_a]
    ], dtype=np.float32)
    return M


def _compute_face_roi(detection, img_w, img_h):
    """
    Compute a rotated ROI (Region of Interest) from a face detection.
    This replicates MediaPipe's FaceDetectionToRoiCalculator.

    detection: dict with 'bbox' [x1, y1, x2, y2] and optionally 'keypoints'
    Returns: dict with 'center', 'size', 'rotation' for the ROI
    """
    x1, y1, x2, y2 = detection['bbox']

    # Compute the center of the detection
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0

    # Compute rotation angle from the eye-midpoint to ear-midpoint line
    # BlazeFace keypoints: 0=right_eye, 1=left_eye, 2=nose, 3=mouth, 4=right_ear, 5=left_ear
    rotation = 0.0
    if 'keypoints' in detection and len(detection['keypoints']) >= 2:
        kp = detection['keypoints']
        # Use eye midpoint to determine face rotation
        r_eye = kp[0]  # right eye
        l_eye = kp[1]  # left eye
        angle = np.arctan2(l_eye[1] - r_eye[1], l_eye[0] - r_eye[0])
        rotation = angle  # face tilt angle

    # Size: use the bounding box diagonal with some padding
    box_w = x2 - x1
    box_h = y2 - y1
    # MediaPipe uses 1.5x scale factor for face ROI
    roi_size = max(box_w, box_h) * 1.5

    return {
        'center': (cx, cy),
        'size': roi_size,
        'rotation': rotation
    }


def _crop_and_align_face(image, roi, output_size=256):
    """
    Crop and align a face from the image using the ROI.
    Returns:
      - cropped: (output_size, output_size, 3) aligned face crop
      - M_inv: 3x3 inverse affine matrix to map landmarks back to original coords
    """
    cx, cy = roi['center']
    size = roi['size']
    angle = roi['rotation']
    h, w = image.shape[:2]

    # Scale factor: map roi_size → output_size
    scale = output_size / size

    # Build the affine transform:
    # 1. Translate center to origin
    # 2. Rotate
    # 3. Scale
    # 4. Translate to output center
    cos_a = np.cos(angle)
    sin_a = np.sin(angle)

    # Forward transform: original image → crop
    M = np.array([
        [cos_a * scale, sin_a * scale, (-cx * cos_a - cy * sin_a) * scale + output_size / 2],
        [-sin_a * scale, cos_a * scale, (cx * sin_a - cy * cos_a) * scale + output_size / 2]
    ], dtype=np.float32)

    cropped = cv2.warpAffine(image, M, (output_size, output_size),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT,
                              borderValue=(0, 0, 0))

    # Inverse transform: crop coordinates → original image coordinates
    # We need a 3x3 matrix for inversion
    M_3x3 = np.vstack([M, [0, 0, 1]])
    M_inv = np.linalg.inv(M_3x3)

    return cropped, M_inv


# ═══════════════════════════════════════════════════════════
#  Mock Objects (Compatible with existing EAR/Head-Pose code)
# ═══════════════════════════════════════════════════════════

class MockLandmark:
    """Mimics mediapipe.framework.formats.landmark_pb2.NormalizedLandmark"""
    __slots__ = ('x', 'y', 'z')

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class MockDetectionResult:
    """Mimics mediapipe.tasks.python.vision.FaceLandmarkerResult"""
    __slots__ = ('face_landmarks',)

    def __init__(self, face_landmarks):
        self.face_landmarks = face_landmarks


# ═══════════════════════════════════════════════════════════
#  Main TensorRT Landmarker Class
# ═══════════════════════════════════════════════════════════

class TensorRTLandmarker:
    """
    Full GPU-accelerated face landmark pipeline:
      1. BlazeFace SSD detector (128x128)
      2. Affine face crop + alignment
      3. FaceMesh landmark regression (256x256)
      4. Reverse-project to original image space
    """

    def __init__(self, detector_path, landmarker_path, cache_dir='./trt_cache'):
        if not ORT_AVAILABLE:
            raise RuntimeError(
                "onnxruntime is not installed. "
                "Install with: pip install onnxruntime-gpu"
            )

        print("[TensorRT] Initializing ONNX Runtime sessions...")
        os.makedirs(cache_dir, exist_ok=True)

        # Configure providers: TensorRT → CUDA → CPU
        providers = [
            ('TensorrtExecutionProvider', {
                'device_id': 0,
                'trt_max_workspace_size': 2147483648,  # 2GB
                'trt_fp16_enable': True,
                'trt_engine_cache_enable': True,
                'trt_engine_cache_path': cache_dir,
            }),
            ('CUDAExecutionProvider', {
                'device_id': 0,
            }),
            'CPUExecutionProvider'
        ]

        self.detector = ort.InferenceSession(detector_path, providers=providers)
        self.landmarker = ort.InferenceSession(landmarker_path, providers=providers)

        # Cache I/O metadata
        self.det_input_name = self.detector.get_inputs()[0].name
        self.det_input_shape = self.detector.get_inputs()[0].shape  # [1, 128, 128, 3]

        self.lan_input_name = self.landmarker.get_inputs()[0].name
        self.lan_input_shape = self.landmarker.get_inputs()[0].shape  # [batch, 256, 256, 3]

        det_provider = self.detector.get_providers()[0]
        lan_provider = self.landmarker.get_providers()[0]
        print(f"[TensorRT] Detector  input: {self.det_input_shape}  provider: {det_provider}")
        print(f"[TensorRT] Landmark  input: {self.lan_input_shape}  provider: {lan_provider}")

        # Pre-generate the 896 SSD anchors
        self.anchors = generate_ssd_anchors()
        print(f"[TensorRT] Generated {len(self.anchors)} SSD anchors")

        # Detection confidence thresholds
        self.det_score_thresh = 0.5
        self.det_iou_thresh = 0.3

        # Performance counters
        self._det_times = []
        self._lan_times = []
        self._total_times = []

    # ── Face Detection ──────────────────────────────────

    def _detect_faces(self, rgb_frame):
        """
        Run BlazeFace face detector on the frame.
        Returns list of detections, each with 'bbox', 'score', 'keypoints'.
        """
        h, w = rgb_frame.shape[:2]

        # Preprocess: resize to 128x128, normalize to [-1, 1]
        det_h = self.det_input_shape[1]
        det_w = self.det_input_shape[2]
        resized = cv2.resize(rgb_frame, (det_w, det_h))
        input_tensor = (resized.astype(np.float32) / 127.5) - 1.0
        input_tensor = np.expand_dims(input_tensor, axis=0)  # (1, 128, 128, 3)

        # Run detector
        t0 = time.time()
        outputs = self.detector.run(None, {self.det_input_name: input_tensor})
        self._det_times.append((time.time() - t0) * 1000)

        # outputs[0] = regressors (1, 896, 16) — box offsets + 6 keypoints
        # outputs[1] = classificators (1, 896, 1) — confidence scores
        raw_boxes = outputs[0][0]    # (896, 16)
        raw_scores = outputs[1][0]   # (896, 1)

        # Apply sigmoid to scores (raw logits) — clamp to avoid overflow
        clamped = np.clip(raw_scores.flatten(), -80.0, 80.0)
        scores = 1.0 / (1.0 + np.exp(-clamped))

        # ── Vectorized SSD box decoding ──
        # BlazeFace regressor format per anchor (16 values):
        #   [cx_offset, cy_offset, w, h,
        #    kp0_x, kp0_y, kp1_x, kp1_y, kp2_x, kp2_y,
        #    kp3_x, kp3_y, kp4_x, kp4_y, kp5_x, kp5_y]
        # All offsets are relative to the anchor center, in detector input pixels.

        anchor_cx = self.anchors[:, 0] * det_w  # (896,)
        anchor_cy = self.anchors[:, 1] * det_h  # (896,)

        # Decode center + size
        cx = raw_boxes[:, 0] + anchor_cx
        cy = raw_boxes[:, 1] + anchor_cy
        bw = raw_boxes[:, 2]
        bh = raw_boxes[:, 3]

        # Convert to [x1, y1, x2, y2] normalized to [0, 1]
        decoded_boxes = np.stack([
            (cx - bw / 2.0) / det_w,
            (cy - bh / 2.0) / det_h,
            (cx + bw / 2.0) / det_w,
            (cy + bh / 2.0) / det_h
        ], axis=1).astype(np.float32)  # (896, 4)

        # Decode keypoints: 6 keypoints × 2 coords = columns 4..15
        kp_raw = raw_boxes[:, 4:16].reshape(-1, 6, 2)  # (896, 6, 2)
        decoded_kps = np.empty_like(kp_raw)
        decoded_kps[:, :, 0] = (kp_raw[:, :, 0] + anchor_cx[:, np.newaxis]) / det_w
        decoded_kps[:, :, 1] = (kp_raw[:, :, 1] + anchor_cy[:, np.newaxis]) / det_h

        # NMS — convert boxes to pixel coords
        pixel_boxes = decoded_boxes.copy()
        pixel_boxes[:, [0, 2]] *= w
        pixel_boxes[:, [1, 3]] *= h

        pixel_kps = decoded_kps.copy()
        pixel_kps[:, :, 0] *= w
        pixel_kps[:, :, 1] *= h

        nms_results = _nms_weighted(pixel_boxes, scores,
                                     keypoints=pixel_kps,
                                     iou_thresh=self.det_iou_thresh,
                                     score_thresh=self.det_score_thresh)

        detections = []
        for blended_box, score, kp in nms_results:
            det = {
                'bbox': blended_box,    # [x1, y1, x2, y2] in pixel coords
                'score': float(score),
                'keypoints': kp          # (6, 2) in pixel coords
            }
            detections.append(det)

        return detections

    # ── Landmark Detection ──────────────────────────────

    def _detect_landmarks(self, rgb_frame, detection):
        """
        Crop the face from the detection, run landmark inference,
        and project landmarks back to original image coordinates.
        Returns: list of 478 MockLandmark objects with normalized (x, y, z).
        """
        h, w = rgb_frame.shape[:2]

        # Compute the face ROI with rotation
        roi = _compute_face_roi(detection, w, h)

        # Crop and align the face
        lan_size = 256  # self.lan_input_shape[1]
        cropped, M_inv = _crop_and_align_face(rgb_frame, roi, output_size=lan_size)

        # Preprocess for the landmark model: normalize to [0, 1]
        input_tensor = cropped.astype(np.float32) / 255.0
        input_tensor = np.expand_dims(input_tensor, axis=0)  # (1, 256, 256, 3)

        # Run inference
        t0 = time.time()
        outputs = self.landmarker.run(None, {self.lan_input_name: input_tensor})
        self._lan_times.append((time.time() - t0) * 1000)

        # Decode outputs:
        # outputs[0] (Identity):   shape (1, 1, 1, 1434) → 478 landmarks × 3 (x, y, z)
        # outputs[1] (Identity_1): shape (1, 1, 1, 1) → face flag (presence score)
        # outputs[2] (Identity_2): shape (1, 1) → unused

        raw_landmarks = outputs[0].reshape(-1)  # (1434,)
        face_flag = outputs[1].flatten()[0]

        # Face presence check (sigmoid)
        face_presence = 1.0 / (1.0 + np.exp(-face_flag))
        if face_presence < 0.5:
            return None

        # Reshape to (478, 3): x, y, z in crop pixel coordinates
        landmarks_crop = raw_landmarks.reshape(478, 3)

        # x, y are in the crop's pixel space (0-256 range)
        # z is a relative depth value

        # Project from crop space back to original image space
        landmarks = []
        for i in range(478):
            # Crop-space coordinates
            crop_x = landmarks_crop[i, 0]
            crop_y = landmarks_crop[i, 1]
            crop_z = landmarks_crop[i, 2]

            # Apply inverse affine transform to get original image pixel coords
            orig = M_inv @ np.array([crop_x, crop_y, 1.0])
            orig_x = orig[0]
            orig_y = orig[1]

            # Normalize to [0, 1] for compatibility with existing code
            norm_x = np.clip(orig_x / w, 0.0, 1.0)
            norm_y = np.clip(orig_y / h, 0.0, 1.0)
            # z is scale-invariant, normalize by crop size
            norm_z = crop_z / lan_size

            landmarks.append(MockLandmark(float(norm_x), float(norm_y), float(norm_z)))

        return landmarks

    # ── Public API ──────────────────────────────────────

    def detect_for_video(self, mp_image, timestamp_ms):
        """
        Drop-in replacement for MediaPipe's landmarker.detect_for_video().
        Takes an mp.Image (or any object with .numpy_view()) and returns
        a MockDetectionResult compatible with the existing EAR/head-pose code.
        """
        t_total_start = time.time()

        # Get the RGB numpy array from the image
        if hasattr(mp_image, 'numpy_view'):
            frame = mp_image.numpy_view()
        else:
            frame = np.array(mp_image)

        # Step 1: Detect faces
        detections = self._detect_faces(frame)

        if not detections:
            self._total_times.append((time.time() - t_total_start) * 1000)
            return MockDetectionResult([])

        # Step 2: Get landmarks for the best detection (highest score)
        best_det = max(detections, key=lambda d: d['score'])
        landmarks = self._detect_landmarks(frame, best_det)

        if landmarks is None:
            self._total_times.append((time.time() - t_total_start) * 1000)
            return MockDetectionResult([])

        self._total_times.append((time.time() - t_total_start) * 1000)
        return MockDetectionResult([landmarks])

    def stats(self):
        """Return performance statistics."""
        def _avg(lst):
            return np.mean(lst) if lst else 0.0

        return {
            'avg_detector_ms': float(_avg(self._det_times)),
            'avg_landmarker_ms': float(_avg(self._lan_times)),
            'avg_total_ms': float(_avg(self._total_times)),
            'total_frames': len(self._total_times),
            'det_provider': self.detector.get_providers()[0],
            'lan_provider': self.landmarker.get_providers()[0],
        }

    def print_stats(self):
        """Print a formatted performance summary."""
        s = self.stats()
        print("\n" + "─" * 50)
        print("  TensorRT LANDMARKER PERFORMANCE")
        print("─" * 50)
        print(f"  Detector:    {s['avg_detector_ms']:.2f} ms/frame  ({s['det_provider']})")
        print(f"  Landmarker:  {s['avg_landmarker_ms']:.2f} ms/frame  ({s['lan_provider']})")
        print(f"  Total:       {s['avg_total_ms']:.2f} ms/frame")
        print(f"  Frames:      {s['total_frames']}")
        print("─" * 50)
