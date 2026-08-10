#!/usr/bin/env bash
# Configure and build Barrier on macOS without deleting the existing build tree.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${BUILD_DIR:-$SCRIPT_DIR/build}"
BUILD_TYPE="${BUILD_TYPE:-Debug}"
LAUNCH=false
BUNDLE=false

usage() {
    cat <<'EOF'
Usage: ./build-macos.sh [--debug|--release] [--bundle] [--launch] [--clean]

Builds Barrier in ./build by default. Override the build directory with BUILD_DIR
or the build type with BUILD_TYPE.

Options:
  --debug     Configure a Debug build (default).
  --release   Configure a Release build.
  --bundle    Create build/bundle/Barrier.app after building.
  --launch    Open the built Barrier GUI after a successful build.
  --clean     Remove the selected build directory before configuring.
  -h, --help  Show this help text.
EOF
}

while (($#)); do
    case "$1" in
        --debug) BUILD_TYPE=Debug ;;
        --release) BUILD_TYPE=Release ;;
        --bundle) BUNDLE=true ;;
        --launch) LAUNCH=true ;;
        --clean)
            if [[ -d "$BUILD_DIR" ]]; then
                rm -rf "$BUILD_DIR"
            fi
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This script builds the macOS target and must be run on macOS." >&2
    exit 1
fi

command -v cmake >/dev/null || {
    echo "CMake is required. Install it with: brew install cmake" >&2
    exit 1
}

command -v brew >/dev/null || {
    echo "Homebrew and qt@5 are required. Install them with: brew install qt@5" >&2
    exit 1
}

QT_PREFIX="$(brew --prefix qt@5 2>/dev/null || true)"
if [[ -z "$QT_PREFIX" || ! -d "$QT_PREFIX/lib/cmake/Qt5" ]]; then
    echo "Qt 5 was not found. Install it with: brew install qt@5" >&2
    exit 1
fi

cmake -S "$SCRIPT_DIR" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DBARRIER_BUILD_TESTS=OFF \
    -DCMAKE_PREFIX_PATH="$QT_PREFIX"

if [[ -n "${CMAKE_BUILD_PARALLEL_LEVEL:-}" ]]; then
    JOBS="$CMAKE_BUILD_PARALLEL_LEVEL"
else
    JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
fi

if [[ "$BUNDLE" == true ]]; then
    cmake --build "$BUILD_DIR" --parallel "$JOBS" --target Barrier_MacOS
else
    cmake --build "$BUILD_DIR" --parallel "$JOBS" --target barrier barrierc barriers
fi

echo "Built: $BUILD_DIR/bin/barrier"

if [[ "$LAUNCH" == true ]]; then
    if [[ "$BUNDLE" == true ]]; then
        open "$BUILD_DIR/bundle/Barrier.app"
    else
        open "$BUILD_DIR/bin/barrier"
    fi
fi
