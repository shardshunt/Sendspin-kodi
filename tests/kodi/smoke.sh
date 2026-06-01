#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/tests/kodi/docker-compose.yml"
DEFAULT_KODI_JSONRPC_PORT=18080
DEFAULT_KODI_WEB_PORT=19090
DEFAULT_CONTROL_API_PORT=59999
DEFAULT_KODI_UDP_PORT=19777
KODI_JSONRPC_PORT="${KODI_JSONRPC_PORT:-$DEFAULT_KODI_JSONRPC_PORT}"
KODI_WEB_PORT="${KODI_WEB_PORT:-$DEFAULT_KODI_WEB_PORT}"
CONTROL_API_PORT="${CONTROL_API_PORT:-$DEFAULT_CONTROL_API_PORT}"
KODI_UDP_PORT="${KODI_UDP_PORT:-$DEFAULT_KODI_UDP_PORT}"
POD_NAME="sendspin-kodi-test"
PODMAN_KODI_CONTAINER="sendspin-kodi-test-kodi"
PODMAN_MOCK_CONTAINER="sendspin-control-mock"
RUNTIME_DIR="$ROOT_DIR/tests/kodi/.runtime"
RUNTIME_ADDON_DIR="$RUNTIME_DIR/addons/plugin.audio.sendspin"
RUNTIME_USERDATA_PODMAN_DIR="$RUNTIME_DIR/userdata-podman"
RUNTIME_USERDATA_COMPOSE_DIR="$RUNTIME_DIR/userdata"

select_free_port() {
  local family="$1"
  local preferred="$2"
  python3 - "$family" "$preferred" <<PY
import contextlib
import socket
import sys
family = sys.argv[1]
preferred = int(sys.argv[2])
if family == 'tcp':
    sock_type = socket.SOCK_STREAM
else:
    sock_type = socket.SOCK_DGRAM
ports = [preferred] + [p for p in range(preferred + 1, preferred + 33)]
for port in ports:
    with contextlib.closing(socket.socket(socket.AF_INET, sock_type)) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
            print(port)
            break
        except OSError:
            continue
else:
    raise SystemExit(f'unable to find free {family} port starting at {preferred}')
PY
}

KODI_JSONRPC_PORT="$(select_free_port tcp "$KODI_JSONRPC_PORT")"
if [ "$KODI_JSONRPC_PORT" != "$DEFAULT_KODI_JSONRPC_PORT" ]; then
  echo "TCP host port $DEFAULT_KODI_JSONRPC_PORT unavailable; using $KODI_JSONRPC_PORT instead." >&2
fi

KODI_WEB_PORT="$(select_free_port tcp "$KODI_WEB_PORT")"
if [ "$KODI_WEB_PORT" != "$DEFAULT_KODI_WEB_PORT" ]; then
  echo "TCP host port $DEFAULT_KODI_WEB_PORT unavailable; using $KODI_WEB_PORT instead." >&2
fi

CONTROL_API_PORT="$(select_free_port tcp "$CONTROL_API_PORT")"
if [ "$CONTROL_API_PORT" != "$DEFAULT_CONTROL_API_PORT" ]; then
  echo "TCP host port $DEFAULT_CONTROL_API_PORT unavailable; using $CONTROL_API_PORT instead." >&2
fi

KODI_UDP_PORT="$(select_free_port udp "$KODI_UDP_PORT")"
if [ "$KODI_UDP_PORT" != "$DEFAULT_KODI_UDP_PORT" ]; then
  echo "UDP host port $DEFAULT_KODI_UDP_PORT unavailable; using $KODI_UDP_PORT instead." >&2
fi

JSONRPC_URL="http://127.0.0.1:${KODI_JSONRPC_PORT}/jsonrpc"
MOCK_URL="http://127.0.0.1:${CONTROL_API_PORT}"

cd "$ROOT_DIR"

cleanup_runtime_tree() {
  if [ ! -e "$RUNTIME_DIR" ]; then
    return 0
  fi

  if rm -rf "$RUNTIME_DIR" 2>/dev/null; then
    return 0
  fi

  if command -v podman >/dev/null 2>&1; then
    podman unshare rm -rf "$RUNTIME_DIR"
    return 0
  fi

  echo "Could not remove $RUNTIME_DIR. Remove it manually, then rerun the smoke test." >&2
  return 1
}

prepare_runtime_tree() {
  cleanup_runtime_tree
  mkdir -p "$RUNTIME_ADDON_DIR"
  mkdir -p "$RUNTIME_USERDATA_PODMAN_DIR"
  mkdir -p "$RUNTIME_USERDATA_COMPOSE_DIR"

  cp -a "$ROOT_DIR/plugin.audio.sendspin/." "$RUNTIME_ADDON_DIR/"
  cp -a "$ROOT_DIR/tests/kodi/userdata-podman/." "$RUNTIME_USERDATA_PODMAN_DIR/"
  cp -a "$ROOT_DIR/tests/kodi/userdata/." "$RUNTIME_USERDATA_COMPOSE_DIR/"

  find "$RUNTIME_ADDON_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
  find "$RUNTIME_ADDON_DIR" -name "*.pyc" -delete
  find "$RUNTIME_ADDON_DIR" -name "*.sync-conflict-*" -delete
}

wait_for_kodi() {
  for _ in $(seq 1 60); do
    if curl -fs "$JSONRPC_URL" \
      -H "Content-Type: application/json" \
      --data '{"jsonrpc":"2.0","method":"JSONRPC.Ping","id":1}' >/dev/null; then
      return 0
    fi
    sleep 2
  done

  echo "Kodi JSON-RPC did not become ready at $JSONRPC_URL" >&2
  return 1
}

wait_for_mock_api() {
  for _ in $(seq 1 30); do
    if curl -fs "$MOCK_URL/test/events" >/dev/null; then
      return 0
    fi
    sleep 1
  done

  echo "Mock control API did not become ready at $MOCK_URL" >&2
  podman logs --tail=80 "$PODMAN_MOCK_CONTAINER" >&2 || true
  return 1
}

curl_retry() {
  local label="$1"
  shift

  for _ in $(seq 1 10); do
    if curl "$@"; then
      return 0
    fi
    echo "Retrying $label..." >&2
    sleep 1
  done

  echo "Final attempt for $label failed:" >&2
  curl "$@"
}

run_smoke_requests() {
  curl_retry "enable addon" -fsS "$JSONRPC_URL" \
    -H "Content-Type: application/json" \
    --data '{"jsonrpc":"2.0","method":"Addons.SetAddonEnabled","params":{"addonid":"plugin.audio.sendspin","enabled":true},"id":2}'
  echo

  curl_retry "open plugin" -fsS "$JSONRPC_URL" \
    -H "Content-Type: application/json" \
    --data '{"jsonrpc":"2.0","method":"Player.Open","params":{"item":{"file":"plugin://plugin.audio.sendspin/"}},"id":3}'
  echo
}

show_podman_diagnostics() {
  echo
  echo "Kodi add-on log lines:"
  podman exec "$PODMAN_KODI_CONTAINER" sh -c \
    "test -f /config/.kodi/temp/kodi.log && grep -Ei 'sendspin|plugin.audio.sendspin|error|exception' /config/.kodi/temp/kodi.log | tail -n 120 || true"

  echo
  echo "Mock control API log lines:"
  podman logs --tail=80 "$PODMAN_MOCK_CONTAINER" || true
}

show_compose_diagnostics() {
  echo
  echo "Kodi add-on log lines:"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" exec -T kodi sh -c \
      "test -f /config/.kodi/temp/kodi.log && grep -Ei 'sendspin|plugin.audio.sendspin|error|exception' /config/.kodi/temp/kodi.log | tail -n 120 || true"
    echo
    echo "Mock control API log lines:"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 sendspin-control-mock || true
  elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    podman compose -f "$COMPOSE_FILE" exec -T kodi sh -c \
      "test -f /config/.kodi/temp/kodi.log && grep -Ei 'sendspin|plugin.audio.sendspin|error|exception' /config/.kodi/temp/kodi.log | tail -n 120 || true"
    echo
    echo "Mock control API log lines:"
    podman compose -f "$COMPOSE_FILE" logs --tail=80 sendspin-control-mock || true
  fi
}

run_with_podman() {
  podman pod rm -f "$POD_NAME" >/dev/null 2>&1 || true
  prepare_runtime_tree

  podman pod create \
    --name "$POD_NAME" \
    -p "$CONTROL_API_PORT":59999 \
    -p "$KODI_JSONRPC_PORT":8080 \
    -p "$KODI_WEB_PORT":9090 \
    -p "$KODI_UDP_PORT":9777/udp

  podman run -d \
    --pod "$POD_NAME" \
    --name "$PODMAN_MOCK_CONTAINER" \
    -v "$ROOT_DIR/tests/kodi/mock_control_api.py:/app/mock_control_api.py:ro,z" \
    python:3.12-slim \
    python /app/mock_control_api.py

  podman run -d \
    --pod "$POD_NAME" \
    --name "$PODMAN_KODI_CONTAINER" \
    -e "PUID=${PUID:-$(id -u)}" \
    -e "PGID=${PGID:-$(id -g)}" \
    -e "TZ=${TZ:-Pacific/Auckland}" \
    -v "$RUNTIME_USERDATA_PODMAN_DIR:/config/.kodi/userdata:Z" \
    -v "$RUNTIME_ADDON_DIR:/config/.kodi/addons/plugin.audio.sendspin:Z" \
    matthuisman/kodi-headless:Omega

  wait_for_mock_api
  wait_for_kodi
  run_smoke_requests
  sleep 8
  show_podman_diagnostics
}

run_with_compose() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
  elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    podman compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
  fi

  prepare_runtime_tree

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" up -d
    wait_for_kodi
    run_smoke_requests
    sleep 8
    show_compose_diagnostics
    return 0
  fi

  if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    podman compose -f "$COMPOSE_FILE" up -d
    wait_for_kodi
    run_smoke_requests
    sleep 8
    show_compose_diagnostics
    return 0
  fi

  return 1
}

case "${SENDSPIN_KODI_RUNTIME:-auto}" in
  podman)
    run_with_podman
    ;;
  compose)
    run_with_compose
    ;;
  auto)
    if command -v podman >/dev/null 2>&1; then
      run_with_podman
    else
      run_with_compose
    fi
    ;;
  *)
    echo "Unknown SENDSPIN_KODI_RUNTIME value: ${SENDSPIN_KODI_RUNTIME}" >&2
    echo "Use auto, podman, or compose." >&2
    exit 2
    ;;
esac
