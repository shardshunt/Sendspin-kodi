#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_SCRIPT="$ROOT_DIR/scripts/build_native_backend_ubuntu_container.sh"
NATIVE_ARCHES="${NATIVE_ARCHES:-x86_64 aarch64 armv7}"
MANIFEST="$ROOT_DIR/build/native-backend-matrix.json"
BUILT_ARCHES=""

mkdir -p "$ROOT_DIR/build"
rm -f -- "$MANIFEST"

for native_arch in $NATIVE_ARCHES; do
  case "$native_arch" in
    x86_64|aarch64|armv7) ;;
    *)
      echo "Unsupported architecture '$native_arch'; use x86_64, aarch64, or armv7" >&2
      exit 1
      ;;
  esac
  case " $BUILT_ARCHES " in
    *" $native_arch "*)
      echo "Architecture '$native_arch' was requested more than once" >&2
      exit 1
      ;;
  esac

  echo "=== Building native Sendspin backend for $native_arch ==="
  rm -rf -- "$ROOT_DIR/build/native"
  NATIVE_ARCH="$native_arch" "$BUILD_SCRIPT"
  BUILT_ARCHES="$BUILT_ARCHES $native_arch"
done

MATRIX_COMPLETE=1
for required_arch in x86_64 aarch64 armv7; do
  case " $BUILT_ARCHES " in
    *" $required_arch "*) ;;
    *) MATRIX_COMPLETE=0 ;;
  esac
done

if [ "$MATRIX_COMPLETE" -eq 1 ]; then
  python3 "$ROOT_DIR/scripts/release.py" --record-native-backend-matrix
  echo "=== Native backend matrix complete ==="
else
  echo "=== Selected native backend builds complete; release matrix not recorded ==="
fi

for native_arch in $NATIVE_ARCHES; do
  printf '%s: %s\n' "$native_arch" \
    "$ROOT_DIR/service.sendspin/resources/bin/$native_arch/sendspin-kodi-backend"
done
