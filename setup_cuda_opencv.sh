#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# setup_cuda_opencv.sh
#
# Build OpenCV 4.8.0 from source WITH CUDA support on Jetson Orin Nano.
#
# The default JetPack OpenCV package (libopencv 4.8.0) is compiled WITHOUT
# CUDA modules.  This script builds a CUDA-enabled OpenCV and installs the
# Python bindings into the project venv.
#
# Estimated time:  ~60–90 minutes on Jetson Orin Nano (8GB).
#
# Usage:
#   cd ~/Developer/mediapipe
#   bash setup_cuda_opencv.sh
#
# After completion, verify with:
#   source venv/bin/activate
#   python3 -c "import cv2; print(cv2.__version__, cv2.cuda.getCudaEnabledDeviceCount())"
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

OPENCV_VERSION="4.8.0"
VENV_DIR="${VENV_DIR:-./venv}"
BUILD_DIR="/tmp/opencv_cuda_build"
CUDA_ARCH="8.7"  # Jetson Orin Nano compute capability
JOBS=$(nproc)

echo "============================================="
echo "  Building OpenCV ${OPENCV_VERSION} with CUDA"
echo "  CUDA Arch: ${CUDA_ARCH}"
echo "  Build jobs: ${JOBS}"
echo "============================================="

# ── Step 0: Install build dependencies ──
echo ""
echo "=== Step 0: Installing build dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential cmake git pkg-config \
    libjpeg-dev libtiff-dev libpng-dev \
    libavcodec-dev libavformat-dev libswscale-dev \
    libv4l-dev libxvidcore-dev libx264-dev \
    libgtk-3-dev libatlas-base-dev gfortran \
    python3-dev python3-numpy \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libtbb-dev

# ── Step 1: Remove pip OpenCV from venv ──
echo ""
echo "=== Step 1: Removing pip OpenCV ==="
source "$VENV_DIR/bin/activate"
pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless 2>/dev/null || true

# ── Step 2: Clone OpenCV source ──
echo ""
echo "=== Step 2: Cloning OpenCV ${OPENCV_VERSION} ==="
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cd "$BUILD_DIR"
git clone --depth 1 --branch ${OPENCV_VERSION} https://github.com/opencv/opencv.git
git clone --depth 1 --branch ${OPENCV_VERSION} https://github.com/opencv/opencv_contrib.git

# ── Step 3: Configure with CMake ──
echo ""
echo "=== Step 3: Configuring CMake (CUDA enabled) ==="
PYTHON3_EXEC=$(which python3)
PYTHON3_INCLUDE=$(python3 -c "from sysconfig import get_paths; print(get_paths()['include'])")
PYTHON3_PACKAGES=$(python3 -c "from sysconfig import get_paths; print(get_paths()['purelib'])")
NUMPY_INCLUDE=$(python3 -c "import numpy; print(numpy.get_include())")

mkdir -p "$BUILD_DIR/opencv/build"
cd "$BUILD_DIR/opencv/build"

cmake \
    -D CMAKE_BUILD_TYPE=RELEASE \
    -D CMAKE_INSTALL_PREFIX=/usr/local \
    -D OPENCV_EXTRA_MODULES_PATH="$BUILD_DIR/opencv_contrib/modules" \
    -D WITH_CUDA=ON \
    -D CUDA_ARCH_BIN=${CUDA_ARCH} \
    -D CUDA_ARCH_PTX="" \
    -D WITH_CUDNN=ON \
    -D OPENCV_DNN_CUDA=ON \
    -D ENABLE_FAST_MATH=ON \
    -D CUDA_FAST_MATH=ON \
    -D WITH_CUBLAS=ON \
    -D WITH_GSTREAMER=ON \
    -D WITH_V4L=ON \
    -D WITH_TBB=ON \
    -D BUILD_opencv_python3=ON \
    -D PYTHON3_EXECUTABLE="$PYTHON3_EXEC" \
    -D PYTHON3_INCLUDE_DIR="$PYTHON3_INCLUDE" \
    -D PYTHON3_PACKAGES_PATH="$PYTHON3_PACKAGES" \
    -D PYTHON3_NUMPY_INCLUDE_DIRS="$NUMPY_INCLUDE" \
    -D BUILD_TESTS=OFF \
    -D BUILD_PERF_TESTS=OFF \
    -D BUILD_EXAMPLES=OFF \
    -D BUILD_opencv_java=OFF \
    -D OPENCV_GENERATE_PKGCONFIG=ON \
    ..

# ── Step 4: Build ──
echo ""
echo "=== Step 4: Building (this takes ~60-90 min) ==="
make -j${JOBS}

# ── Step 5: Install ──
echo ""
echo "=== Step 5: Installing ==="
sudo make install
sudo ldconfig

# ── Step 6: Link into venv ──
echo ""
echo "=== Step 6: Linking into venv ==="
# Find the built cv2 .so
CV2_SO=$(find /usr/local/lib -name "cv2*.so" -path "*/python3*" 2>/dev/null | head -1)
if [ -z "$CV2_SO" ]; then
    echo "ERROR: Could not find built cv2 .so file"
    exit 1
fi

CV2_DIR=$(dirname "$CV2_SO")
SITE_PACKAGES="$VENV_DIR/lib/python3.10/site-packages"

# Remove any existing cv2
rm -rf "$SITE_PACKAGES/cv2" "$SITE_PACKAGES/cv2.so"

# Symlink
ln -sf "$CV2_DIR/cv2" "$SITE_PACKAGES/cv2" 2>/dev/null || \
ln -sf "$CV2_SO" "$SITE_PACKAGES/cv2.so"

echo "Linked $CV2_SO into venv"

# ── Step 7: Verify ──
echo ""
echo "=== Step 7: Verification ==="
python3 -c "
import cv2
print('OpenCV version:', cv2.__version__)
bi = cv2.getBuildInformation()
for line in bi.split(chr(10)):
    if 'CUDA' in line.upper() or 'CUDNN' in line.upper():
        print(line)
try:
    n = cv2.cuda.getCudaEnabledDeviceCount()
    print(f'CUDA devices: {n}')
    if n > 0:
        cv2.cuda.printShortCudaDeviceInfo(0)
        print()
        print('SUCCESS: CUDA-accelerated OpenCV is ready!')
    else:
        print('WARNING: Built with CUDA but no devices found')
except Exception as e:
    print(f'CUDA check failed: {e}')
"

echo ""
echo "============================================="
echo "  Build complete!"
echo "  Test with: MP_BENCHMARK=1 python3 face_detect_mediapipe.py"
echo "============================================="
