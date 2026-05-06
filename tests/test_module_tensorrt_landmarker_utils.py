import numpy as np
import pytest

import modules.module_tensorrt_landmarker as trt


def test_generate_ssd_anchors_has_expected_shape_and_normalized_centers():
    anchors = trt.generate_ssd_anchors()

    assert anchors.shape == (896, 4)
    assert anchors.dtype == np.float32
    assert np.all(anchors[:, 0] > 0)
    assert np.all(anchors[:, 0] < 1)
    assert np.all(anchors[:, 1] > 0)
    assert np.all(anchors[:, 1] < 1)


def test_compute_iou_handles_overlap_and_degenerate_union():
    assert trt._compute_iou([0, 0, 2, 2], [1, 1, 3, 3]) == pytest.approx(1 / 7)
    assert trt._compute_iou([0, 0, 0, 0], [1, 1, 1, 1]) == 0.0


def test_weighted_nms_filters_scores_and_blends_overlapping_boxes():
    boxes = np.array(
        [
            [0.0, 0.0, 2.0, 2.0],
            [0.2, 0.0, 2.2, 2.0],
            [10.0, 10.0, 12.0, 12.0],
            [20.0, 20.0, 21.0, 21.0],
        ],
        dtype=np.float32,
    )
    scores = np.array([0.9, 0.6, 0.8, 0.1], dtype=np.float32)
    keypoints = np.arange(4 * 6 * 2, dtype=np.float32).reshape(4, 6, 2)

    keep = trt._nms_weighted(boxes, scores, keypoints=keypoints, iou_thresh=0.3, score_thresh=0.5)

    assert len(keep) == 2
    blended_box, blended_score, blended_keypoints = keep[0]
    expected_x1 = (0.9 * 0.0 + 0.6 * 0.2) / 1.5
    expected_x2 = (0.9 * 2.0 + 0.6 * 2.2) / 1.5
    assert blended_score == pytest.approx(0.9)
    assert blended_box.tolist() == pytest.approx([expected_x1, 0.0, expected_x2, 2.0])
    assert np.array_equal(blended_keypoints, keypoints[0])
    assert keep[1][0].tolist() == pytest.approx([10.0, 10.0, 12.0, 12.0])


def test_compute_face_roi_uses_box_center_padding_and_eye_rotation():
    detection = {
        "bbox": [10.0, 20.0, 30.0, 50.0],
        "keypoints": np.array([[10.0, 20.0], [20.0, 30.0]], dtype=np.float32),
    }

    roi = trt._compute_face_roi(detection, img_w=100, img_h=100)

    assert roi["center"] == (20.0, 35.0)
    assert roi["size"] == 45.0
    assert roi["rotation"] == pytest.approx(np.pi / 4)


def test_crop_and_align_face_returns_crop_and_inverse_mapping():
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    image[10, 10] = [255, 0, 0]
    roi = {"center": (10.0, 10.0), "size": 10.0, "rotation": 0.0}

    crop, inverse = trt._crop_and_align_face(image, roi, output_size=10)

    assert crop.shape == (10, 10, 3)
    assert inverse.shape == (3, 3)
    center = inverse @ np.array([5.0, 5.0, 1.0])
    assert center.tolist() == pytest.approx([10.0, 10.0, 1.0])
