#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON_XML="$ROOT_DIR/plugin.audio.sendspin/addon.xml"

if [ ! -f "$ADDON_XML" ]; then
  echo "Missing addon.xml at $ADDON_XML" >&2
  exit 1
fi

IMAGE_NAME="${SENDSPIN_IMAGE_NAME:-ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi}"
EXTRACTED_VERSION="$(ADDON_XML="$ADDON_XML" python3 - <<'PY'
import os
import sys
import xml.etree.ElementTree as ET
try:
    tree = ET.parse(os.environ['ADDON_XML'])
    root = tree.getroot()
    print(root.attrib.get('version', ''))
except Exception as exc:
    print(f'ERROR: {exc}', file=sys.stderr)
    sys.exit(1)
PY
)"

if [ -z "${SENDSPIN_IMAGE_TAG:-}" ]; then
  IMAGE_TAG="$EXTRACTED_VERSION"
else
  IMAGE_TAG="$SENDSPIN_IMAGE_TAG"
fi

if [ -z "$IMAGE_TAG" ]; then
  echo "Failed to determine image tag from $ADDON_XML" >&2
  exit 1
fi

FULL_IMAGE="$IMAGE_NAME:$IMAGE_TAG"
CONTAINER_NAME="sendspin-image-version-test"

echo "Reading addon metadata from: $ADDON_XML"
echo "Extracted addon version: $EXTRACTED_VERSION"
echo "Using Docker image tag: $IMAGE_TAG"
echo "Testing Docker image pull and start for: $FULL_IMAGE"

docker pull "$FULL_IMAGE"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -d --name "$CONTAINER_NAME" --entrypoint sh "$FULL_IMAGE" -c 'command -v sendspin >/dev/null && echo OK && sleep 10'

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 2

if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" != "true" ]; then
  echo "Container did not start successfully." >&2
  docker logs "$CONTAINER_NAME" >&2 || true
  exit 1
fi

echo "Container is running. Logs:"
docker logs "$CONTAINER_NAME"

echo "Docker image version test passed for $FULL_IMAGE"
