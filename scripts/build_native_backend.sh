#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_DIR="${ROOT_DIR}/build/native"
NATIVE_ARCH="${NATIVE_ARCH:-}"

CMAKE_CROSS_ARGS=""
HOST_ARCH=$(uname -m)

case "$NATIVE_ARCH" in
  ""|x86_64)
    case "$HOST_ARCH" in
      x86_64|amd64) ;;
      *)
        CMAKE_CROSS_ARGS="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=x86_64 -DCMAKE_C_COMPILER=x86_64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=x86_64-linux-gnu-g++"
        export PKG_CONFIG_PATH="/usr/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
        ;;
    esac
    ;;
  aarch64)
    case "$HOST_ARCH" in
      aarch64|arm64) ;;
      *)
        CMAKE_CROSS_ARGS="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++"
        export PKG_CONFIG_PATH="/usr/lib/aarch64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
        ;;
    esac
    ;;
  armv7)
    case "$HOST_ARCH" in
      armv7*|armhf) ;;
      *)
        CMAKE_CROSS_ARGS="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=armv7l -DCMAKE_C_COMPILER=arm-linux-gnueabihf-gcc -DCMAKE_CXX_COMPILER=arm-linux-gnueabihf-g++"
        export PKG_CONFIG_PATH="/usr/lib/arm-linux-gnueabihf/pkgconfig:${PKG_CONFIG_PATH:-}"
        ;;
    esac
    ;;
  *) echo "NATIVE_ARCH must be x86_64, aarch64, or armv7" >&2; exit 1 ;;
esac

# shellcheck disable=SC2086
cmake -S "${ROOT_DIR}/native" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_INSTALL_PREFIX="${ROOT_DIR}" \
  $CMAKE_CROSS_ARGS
cmake --build "${BUILD_DIR}" --parallel

if [ -n "$NATIVE_ARCH" ]; then
  OUTPUT_DIR="${ROOT_DIR}/service.sendspin/resources/bin/${NATIVE_ARCH}"
else
  OUTPUT_DIR="${ROOT_DIR}/service.sendspin/resources/bin"
fi
mkdir -p "$OUTPUT_DIR"
install -m 0755 \
  "${BUILD_DIR}/sendspin-kodi-backend" \
  "${OUTPUT_DIR}/sendspin-kodi-backend"

printf 'Packaged native backend at %s\n' \
  "${OUTPUT_DIR}/sendspin-kodi-backend"
