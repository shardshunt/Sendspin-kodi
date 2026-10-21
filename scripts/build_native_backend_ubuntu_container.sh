#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE_NAME="localhost/sendspin-kodi-native-builder:ubuntu2204"

podman build --tag "$IMAGE_NAME" --file "$ROOT_DIR/Containerfile.native-build-ubuntu" "$ROOT_DIR"
podman run --rm --userns=keep-id --volume "$ROOT_DIR:/workspace:Z" \
  --workdir /workspace "$IMAGE_NAME" scripts/build_native_backend.sh
