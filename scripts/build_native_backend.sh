#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_DIR="${ROOT_DIR}/build/native"

cmake -S "${ROOT_DIR}/native" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_INSTALL_PREFIX="${ROOT_DIR}"
cmake --build "${BUILD_DIR}" --parallel

mkdir -p "${ROOT_DIR}/service.sendspin/resources/bin"
install -m 0755 \
  "${BUILD_DIR}/sendspin-kodi-backend" \
  "${ROOT_DIR}/service.sendspin/resources/bin/sendspin-kodi-backend"

printf 'Packaged native backend at %s\n' \
  "${ROOT_DIR}/service.sendspin/resources/bin/sendspin-kodi-backend"
