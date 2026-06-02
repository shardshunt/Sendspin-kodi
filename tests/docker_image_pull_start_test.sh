#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS_XML="$ROOT_DIR/service.sendspin/resources/settings.xml"
VERSION_FILE="$ROOT_DIR/service.sendspin/docker_image_version.txt"

if [ ! -f "$SETTINGS_XML" ]; then
  echo "Missing settings.xml at $SETTINGS_XML" >&2
  exit 1
fi

if [ ! -f "$VERSION_FILE" ]; then
  echo "Missing docker image version file at $VERSION_FILE" >&2
  exit 1
fi

read -r DEFAULT_IMAGE_NAME < <(SETTINGS_XML="$SETTINGS_XML" python3 - <<'PY'
import os
import sys
import xml.etree.ElementTree as ET

try:
    tree = ET.parse(os.environ["SETTINGS_XML"])
    root = tree.getroot()
except Exception as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(1)

settings = {
    item.attrib.get("id"): item.attrib.get("default", "")
    for item in root.findall(".//setting")
}
print(settings.get("docker_image_name", ""))
PY
)

DEFAULT_IMAGE_TAG="$(<"$VERSION_FILE")"

IMAGE_NAME="${SENDSPIN_IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
IMAGE_TAG="${SENDSPIN_IMAGE_TAG:-$DEFAULT_IMAGE_TAG}"

if [ -z "$IMAGE_NAME" ]; then
  echo "Failed to determine image name from $SETTINGS_XML" >&2
  exit 1
fi

if [ -z "$IMAGE_TAG" ]; then
  echo "Failed to determine image tag from $VERSION_FILE" >&2
  exit 1
fi

FULL_IMAGE="$IMAGE_NAME:$IMAGE_TAG"
CONTAINER_NAME="sendspin-image-version-test"

echo "Reading Docker image settings from: $SETTINGS_XML"
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
