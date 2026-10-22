#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
NATIVE_ARCH="${NATIVE_ARCH:-}"
IMAGE_NAME="localhost/sendspin-kodi-native-builder:ubuntu2204"

case "$NATIVE_ARCH" in
  ""|x86_64|aarch64|armv7) ;;
  *) echo "NATIVE_ARCH must be x86_64, aarch64, or armv7" >&2; exit 1 ;;
esac

podman build --platform linux/amd64 --tag "$IMAGE_NAME" \
  --file "$ROOT_DIR/Containerfile.native-build-ubuntu" "$ROOT_DIR"

if [ -n "${NATIVE_ARCH:-}" ]; then
  podman run --rm --platform linux/amd64 --userns=keep-id \
    --env "NATIVE_ARCH=${NATIVE_ARCH}" \
    --volume "$ROOT_DIR:/workspace:Z" --workdir /workspace \
    "$IMAGE_NAME" scripts/build_native_backend.sh
else
  podman run --rm --platform linux/amd64 --userns=keep-id \
    --volume "$ROOT_DIR:/workspace:Z" \
    --workdir /workspace "$IMAGE_NAME" scripts/build_native_backend.sh
fi
