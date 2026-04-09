from module_gpu_preprocessor import CUDA_AVAILABLE, CUDA_INFO, GpuPreprocessor
import numpy as np

print("CUDA available:", CUDA_AVAILABLE)
print("Device:", CUDA_INFO.get("device_name"))
print()

resolutions = [(480, 640), (720, 1280), (1080, 1920)]

for h, w in resolutions:
    dummy = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    
    gpu_pp = GpuPreprocessor(use_gpu=True)
    cpu_pp = GpuPreprocessor(use_gpu=False)
    
    # Warmup
    for _ in range(50):
        gpu_pp.bgr_to_rgb(dummy)
        cpu_pp.bgr_to_rgb(dummy)
    gpu_pp.reset_stats()
    cpu_pp.reset_stats()
    
    # Benchmark
    for _ in range(500):
        gpu_pp.bgr_to_rgb(dummy)
        cpu_pp.bgr_to_rgb(dummy)
    
    gs = gpu_pp.stats()
    cs = cpu_pp.stats()
    speedup = cs["avg_preprocess_ms"] / gs["avg_preprocess_ms"] if gs["avg_preprocess_ms"] > 0 else 0
    print("%dx%d  CPU: %.4f ms  CUDA: %.4f ms  Speedup: %.2fx" % (w, h, cs["avg_preprocess_ms"], gs["avg_preprocess_ms"], speedup))

print()
print("Note: GPU wins at higher resolutions where compute > transfer overhead")
