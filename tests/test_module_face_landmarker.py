import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest


for name in (
    "mediapipe",
    "mediapipe.tasks",
    "mediapipe.tasks.python",
    "mediapipe.tasks.python.vision",
):
    sys.modules.setdefault(name, types.ModuleType(name))

import modules.module_face_landmarker as module_face_landmarker


def _landmarks(points):
    size = max(points) + 1
    result = [SimpleNamespace(x=0.0, y=0.0, z=0.0) for _ in range(size)]
    for index, coords in points.items():
        result[index] = SimpleNamespace(x=coords[0], y=coords[1], z=coords[2] if len(coords) > 2 else 0.0)
    return result


def test_calculate_ear_uses_scaled_landmark_geometry():
    landmarks = _landmarks(
        {
            0: (0.0, 0.0),
            1: (0.25, 0.25),
            2: (0.75, 0.25),
            3: (1.0, 0.0),
            4: (0.75, -0.25),
            5: (0.25, -0.25),
        }
    )

    ear = module_face_landmarker.calculate_ear(landmarks, [0, 1, 2, 3, 4, 5], (100, 100))

    assert ear == pytest.approx(0.5)


def test_calculate_ear_returns_zero_for_degenerate_horizontal_distance():
    landmarks = _landmarks({0: (0.5, 0.0), 1: (0.5, 0.2), 2: (0.5, 0.3), 3: (0.5, 0.0), 4: (0.5, -0.3), 5: (0.5, -0.2)})

    assert module_face_landmarker.calculate_ear(landmarks, [0, 1, 2, 3, 4, 5], (100, 100)) == 0.0


def test_get_head_vertical_position_normalizes_nose_between_forehead_and_chin():
    landmarks = _landmarks({1: (0.5, 0.45), 10: (0.5, 0.20), 152: (0.5, 0.70)})

    assert module_face_landmarker.get_head_vertical_position(landmarks) == pytest.approx(0.5)


def test_get_head_vertical_position_falls_back_when_face_height_is_tiny():
    landmarks = _landmarks({1: (0.5, 0.45), 10: (0.5, 0.20), 152: (0.5, 0.2005)})

    assert module_face_landmarker.get_head_vertical_position(landmarks) == 0.45


def test_draw_landmarks_returns_original_image_when_no_faces():
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    result = module_face_landmarker.draw_landmarks_on_image(image, SimpleNamespace(face_landmarks=[]))

    assert result is image
