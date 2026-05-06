import numpy as np

import modules.module_gpu_preprocessor as module_gpu_preprocessor
from modules.module_gpu_preprocessor import GpuPreprocessor


def test_cpu_preprocessor_converts_bgr_to_rgb_and_tracks_stats(monkeypatch):
    monkeypatch.setattr(module_gpu_preprocessor, "CUDA_AVAILABLE", False)
    frame = np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)
    preprocessor = GpuPreprocessor(use_gpu=True)

    result = preprocessor.bgr_to_rgb(frame)
    stats = preprocessor.stats()

    assert preprocessor.using_gpu is False
    assert preprocessor.backend_label == "CPU"
    assert result.tolist() == [[[3, 2, 1], [6, 5, 4]]]
    assert stats["backend"] == "CPU"
    assert stats["frames_processed"] == 1
    assert stats["avg_preprocess_ms"] >= 0
    assert stats["total_preprocess_s"] >= 0


def test_reset_stats_clears_accumulators(monkeypatch):
    monkeypatch.setattr(module_gpu_preprocessor, "CUDA_AVAILABLE", False)
    preprocessor = GpuPreprocessor(use_gpu=False)
    preprocessor.bgr_to_rgb(np.zeros((1, 1, 3), dtype=np.uint8))

    preprocessor.reset_stats()

    assert preprocessor.stats() == {
        "backend": "CPU",
        "frames_processed": 0,
        "avg_preprocess_ms": 0.0,
        "total_preprocess_s": 0.0,
    }
