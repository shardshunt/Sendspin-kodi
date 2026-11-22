#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTAINER_ENGINE="${CONTAINER_ENGINE:-podman}"
BUILD_BACKENDS=true
RUN_KODI_SMOKE=true

show_help() {
  cat <<'EOF'
Usage: tests/kodi/distro_smoke.sh [--skip-build] [--skip-kodi]

Build the x86_64 and i386 native backends, run the Kodi add-on smoke test,
then check backend startup and virtual ALSA playback in Ubuntu and Fedora
containers.

Options:
  --skip-build  Reuse existing x86_64 and i386 backend binaries.
  --skip-kodi   Skip the basic Kodi smoke; useful after api_scenarios.sh.
  -h, --help    Show this help.

Environment:
  CONTAINER_ENGINE  Container engine to use (default: podman).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)
      BUILD_BACKENDS=false
      shift
      ;;
    --skip-kodi)
      RUN_KODI_SMOKE=false
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 2
      ;;
  esac
done

command -v "$CONTAINER_ENGINE" >/dev/null 2>&1 || {
  echo "Container engine not found: $CONTAINER_ENGINE" >&2
  exit 1
}

if [[ "$BUILD_BACKENDS" == true ]]; then
  CONTAINER_ENGINE="$CONTAINER_ENGINE" \
    "$ROOT_DIR/scripts/build_native_backends_all.sh" \
      --arch x86_64 --arch i386 --serial --no-record-matrix
fi

for arch in x86_64 i386; do
  backend="$ROOT_DIR/service.sendspin/resources/bin/$arch/sendspin-kodi-backend"
  [[ -x "$backend" ]] || {
    echo "Missing executable $arch backend: $backend" >&2
    echo "Build both backends or omit --skip-build." >&2
    exit 1
  }
done

if [[ "$RUN_KODI_SMOKE" == true ]]; then
  echo "=== Headless Kodi add-on smoke ==="
  if [[ "$CONTAINER_ENGINE" == podman ]]; then
    SENDSPIN_KODI_RUNTIME=podman CONTAINER_ENGINE="$CONTAINER_ENGINE" \
      "$ROOT_DIR/tests/kodi/smoke.sh"
  else
    SENDSPIN_KODI_RUNTIME=compose CONTAINER_ENGINE="$CONTAINER_ENGINE" \
      "$ROOT_DIR/tests/kodi/smoke.sh"
  fi
fi

for image in ubuntu:24.04 fedora:latest; do
  echo "=== Native backend and virtual ALSA smoke: $image ==="
  "$CONTAINER_ENGINE" run --rm \
    --volume "$ROOT_DIR/service.sendspin/resources/bin:/backend:ro,z" \
    --volume "$ROOT_DIR/tests/kodi/native_container_smoke.sh:/smoke.sh:ro,z" \
    "$image" bash /smoke.sh
done
