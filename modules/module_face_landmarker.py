import cv2
import math
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

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
