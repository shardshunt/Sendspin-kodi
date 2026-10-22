#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_SCRIPT="$ROOT_DIR/scripts/build_native_backend_ubuntu_container.sh"
NATIVE_ARCHES="${NATIVE_ARCHES:-x86_64 aarch64 armv7}"

for native_arch in $NATIVE_ARCHES; do
  case "$native_arch" in
    x86_64|aarch64|armv7) ;;
    *)
      echo "Unsupported architecture '$native_arch'; use x86_64, aarch64, or armv7" >&2
      exit 1
      ;;
  esac

  echo "=== Building native Sendspin backend for $native_arch ==="
  rm -rf -- "$ROOT_DIR/build/native"
  NATIVE_ARCH="$native_arch" "$BUILD_SCRIPT"
done

echo "=== Native backend matrix complete ==="
for native_arch in $NATIVE_ARCHES; do
  printf '%s: %s\n' "$native_arch" \
    "$ROOT_DIR/service.sendspin/resources/bin/$native_arch/sendspin-kodi-backend"
done
