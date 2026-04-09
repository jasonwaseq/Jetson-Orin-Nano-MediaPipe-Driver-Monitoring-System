#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# setup_cuda_opencv.sh
#
# Enable CUDA-accelerated OpenCV on the Jetson Orin Nano.
#
# Problem:
#   pip-installed opencv-python lacks CUDA.  The JetPack system OpenCV 4.8.0
#   at /usr/lib/python3.10/dist-packages has CUDA but is broken by numpy 2.x.
#
# Solution:
#   1. Remove pip opencv packages from the venv
#   2. Downgrade numpy to 1.x (compatible with JetPack OpenCV)
#   3. Symlink system OpenCV into the venv so it can find the CUDA build
#
# Usage:
#   cd ~/Developer/mediapipe
#   bash setup_cuda_opencv.sh
#
# After running, verify with:
#   source venv/bin/activate
#   python3 -c "import cv2; print(cv2.__version__, cv2.cuda.getCudaEnabledDeviceCount())"
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

VENV_DIR="${VENV_DIR:-./venv}"
SITE_PACKAGES="$VENV_DIR/lib/python3.10/site-packages"
SYS_CV2="/usr/lib/python3.10/dist-packages/cv2"

echo "=== Step 1: Remove pip-installed OpenCV from venv ==="
source "$VENV_DIR/bin/activate"
pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless 2>/dev/null || true

echo ""
echo "=== Step 2: Downgrade numpy to 1.x (JetPack OpenCV compat) ==="
pip install 'numpy<2'

echo ""
echo "=== Step 3: Symlink system JetPack OpenCV into venv ==="
if [ ! -d "$SYS_CV2" ]; then
    echo "ERROR: System OpenCV not found at $SYS_CV2"
    echo "Install with:  sudo apt install libopencv-python libopencv"
    exit 1
fi

# Remove any leftover cv2 in venv site-packages
rm -rf "$SITE_PACKAGES/cv2" "$SITE_PACKAGES/cv2.so"

# Create symlink
ln -sf "$SYS_CV2" "$SITE_PACKAGES/cv2"
echo "Symlinked $SYS_CV2 -> $SITE_PACKAGES/cv2"

echo ""
echo "=== Step 4: Verify ==="
python3 -c "
import cv2
print('OpenCV version:', cv2.__version__)
try:
    n = cv2.cuda.getCudaEnabledDeviceCount()
    print(f'CUDA devices: {n}')
    if n > 0:
        cv2.cuda.printShortCudaDeviceInfo(0)
        print('SUCCESS: CUDA-accelerated OpenCV is ready!')
    else:
        print('WARNING: OpenCV loaded but reports 0 CUDA devices')
except Exception as e:
    print(f'CUDA check failed: {e}')
"

echo ""
echo "=== Done ==="
echo "You can now run:  MP_BENCHMARK=1 python3 face_detect_mediapipe.py"
