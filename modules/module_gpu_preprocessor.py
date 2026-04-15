"""
GPU-accelerated frame preprocessing using OpenCV CUDA.

Detects CUDA availability at import time and provides a ``GpuPreprocessor``
that transparently falls back to CPU when the GPU path is unavailable.
"""

import time
import cv2


# ── CUDA / GPU Detection ──────────────────────────────────────────────────────

def detect_cuda():
    """Detect OpenCV CUDA availability and return device info dict."""
    info = {
        "available": False,
        "device_count": 0,
        "device_name": None,
        "cv2_cuda_enabled": False,
    }
    try:
        count = cv2.cuda.getCudaEnabledDeviceCount()
        info["device_count"] = count
        info["cv2_cuda_enabled"] = count > 0
        if count > 0:
            cv2.cuda.setDevice(0)
            dev = cv2.cuda.getDevice()
            try:
                cv2.cuda.printShortCudaDeviceInfo(dev)
            except Exception:
                pass
            info["available"] = True
            info["device_name"] = f"CUDA Device {dev}"
    except cv2.error:
        pass  # OpenCV built without CUDA
    except AttributeError:
        pass  # Very old OpenCV or stub build
    return info


CUDA_INFO = detect_cuda()
CUDA_AVAILABLE = CUDA_INFO["available"]


# ── GPU Preprocessor ──────────────────────────────────────────────────────────

class GpuPreprocessor:
    """GPU-accelerated frame preprocessing using OpenCV CUDA.

    Reuses GpuMat buffers across frames to avoid per-frame allocation
    overhead.  Falls back to CPU transparently when CUDA is unavailable.

    Parameters
    ----------
    use_gpu : bool
        Request GPU acceleration.  Actual usage depends on ``CUDA_AVAILABLE``.
    """

    def __init__(self, use_gpu=True):
        self._use_gpu = use_gpu and CUDA_AVAILABLE
        # Pre-allocated GPU mats — lazily sized on first frame
        self._gpu_bgr = None
        self._gpu_rgb = None
        self._frame_shape = None
        self._frames_processed = 0
        self._time_accum = 0.0

    @property
    def using_gpu(self):
        return self._use_gpu

    @property
    def backend_label(self):
        return "CUDA" if self._use_gpu else "CPU"

    def bgr_to_rgb(self, frame):
        """Convert a BGR frame to RGB, using GPU when available."""
        t0 = time.time()

        if not self._use_gpu:
            result = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            # Lazy-init or reinit if frame size changed
            h, w = frame.shape[:2]
            if self._frame_shape != (h, w):
                self._gpu_bgr = cv2.cuda_GpuMat(h, w, cv2.CV_8UC3)
                self._gpu_rgb = cv2.cuda_GpuMat(h, w, cv2.CV_8UC3)
                self._frame_shape = (h, w)

            self._gpu_bgr.upload(frame)
            cv2.cuda.cvtColor(self._gpu_bgr, cv2.COLOR_BGR2RGB, self._gpu_rgb)
            result = self._gpu_rgb.download()

        self._time_accum += time.time() - t0
        self._frames_processed += 1
        return result

    def reset_stats(self):
        """Clear accumulated statistics (useful between benchmark runs)."""
        self._frames_processed = 0
        self._time_accum = 0.0

    def stats(self):
        """Return a dict of processing statistics."""
        avg_ms = 0.0
        if self._frames_processed > 0:
            avg_ms = (self._time_accum / self._frames_processed) * 1000
        return {
            "backend": self.backend_label,
            "frames_processed": self._frames_processed,
            "avg_preprocess_ms": round(avg_ms, 4),
            "total_preprocess_s": round(self._time_accum, 4),
        }
