#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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
RUNTIME_ADDON_DIR="$RUNTIME_DIR/addons/service.sendspin"
RUNTIME_USERDATA_PODMAN_DIR="$RUNTIME_DIR/userdata-podman"

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

show_failure_diagnostics() {
  echo
  echo "Scenario failed. Recent container logs:" >&2
  podman logs --tail=80 "$PODMAN_MOCK_CONTAINER" >&2 || true
  podman logs --tail=80 "$PODMAN_KODI_CONTAINER" >&2 || true
}

trap show_failure_diagnostics ERR

cleanup_runtime_tree() {
  if [ ! -e "$RUNTIME_DIR" ]; then
    return 0
  fi

  if rm -rf "$RUNTIME_DIR" 2>/dev/null; then
    return 0
  fi

  podman unshare rm -rf "$RUNTIME_DIR"
}

prepare_runtime_tree() {
  cleanup_runtime_tree
  mkdir -p "$RUNTIME_ADDON_DIR" "$RUNTIME_USERDATA_PODMAN_DIR"
  cp -a "$ROOT_DIR/service.sendspin/." "$RUNTIME_ADDON_DIR/"
  cp -a "$ROOT_DIR/tests/kodi/userdata-podman/." "$RUNTIME_USERDATA_PODMAN_DIR/"

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

jsonrpc() {
  curl_retry "Kodi JSON-RPC" -fsS "$JSONRPC_URL" -H "Content-Type: application/json" --data "$1"
  echo
}

mock_post() {
  curl_retry "mock POST $1" -fsS "$MOCK_URL$1" -H "Content-Type: application/json" --data "$2" >/dev/null
}

open_plugin_url() {
  jsonrpc "{\"jsonrpc\":\"2.0\",\"method\":\"Player.Open\",\"params\":{\"item\":{\"file\":\"$1\"}},\"id\":3}" >/dev/null
}

run_plugin_action() {
  jsonrpc "{\"jsonrpc\":\"2.0\",\"method\":\"Files.GetDirectory\",\"params\":{\"directory\":\"plugin://service.sendspin/?action=$1\",\"media\":\"music\"},\"id\":4}" >/dev/null
}

start_harness() {
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
    -v "$RUNTIME_ADDON_DIR:/config/.kodi/addons/service.sendspin:Z" \
    matthuisman/kodi-headless:Omega

  wait_for_mock_api
  wait_for_kodi
  mock_post "/test/reset" "{}"
  jsonrpc '{"jsonrpc":"2.0","method":"Addons.SetAddonEnabled","params":{"addonid":"service.sendspin","enabled":true},"id":2}' >/dev/null
}

run_state_scenarios() {
  open_plugin_url "plugin://service.sendspin/"
  sleep 3

  mock_post "/test/state" '{"track":{},"playback":{},"volume":{}}'
  sleep 1

  mock_post "/test/state" '{"track":{"title":"Scenario Track","artist":"Scenario Artist","album":"Scenario Album","artwork_url":""},"playback":{"position":12,"duration":240,"speed":1},"volume":{"volume":75,"muted":false}}'
  sleep 2

  mock_post "/test/state" '{"track":{"title":"Paused Scenario","artist":"Scenario Artist","album":"Scenario Album","artwork_url":""},"playback":{"position":18,"duration":240,"speed":0},"volume":{"volume":20,"muted":true}}'
  sleep 2

}

run_plugin_command_scenarios() {
  for action in play pause playpause toggle_play_pause next previous; do
    run_plugin_action "$action"
    sleep 1
  done
}

run_direct_control_command_scenarios() {
  mock_post "/control" '{"command":"set_volume","volume":42,"muted":false}'
  mock_post "/control" '{"command":"seek","position":120.5}'
}

run_delay_setting_scenario() {
  python3 - <<PY
import xml.etree.ElementTree as ET
path = "$RUNTIME_USERDATA_PODMAN_DIR/addon_data/service.sendspin/settings.xml"
tree = ET.parse(path)
root = tree.getroot()
for setting in root.findall('setting'):
    if setting.get('id') == 'delay_ms':
        setting.text = '250'
        break
else:
    new_setting = ET.SubElement(root, 'setting', {'id': 'delay_ms'})
    new_setting.text = '250'
tree.write(path, encoding='utf-8', xml_declaration=False)
PY

  for _ in $(seq 1 15); do
    if curl_retry "mock GET /test/events" -fsS "$MOCK_URL/test/events" | \
      python3 -c 'import json,sys;events=json.load(sys.stdin)["events"];print("set_delay" in [e["payload"].get("command") for e in events if e["type"]=="control"])' | grep -q True; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for set_delay after updating delay_ms setting." >&2
  return 1
}

assert_events() {
  local events_file="$RUNTIME_DIR/events.json"
  curl_retry "mock GET /test/events" -fsS "$MOCK_URL/test/events" -o "$events_file"
  python -c '
import json
import sys

events = json.load(open(sys.argv[1], encoding="utf-8"))["events"]
commands = [event["payload"].get("command") for event in events if event["type"] == "control"]
missing = [command for command in sys.argv[2:] if command not in commands]
state_count = sum(1 for event in events if event["type"] == "state")

if missing:
    raise SystemExit(f"Missing control commands: {missing}; saw {commands}")
if state_count < 3:
    raise SystemExit(f"Expected at least 3 /state polls; saw {state_count}")

print(f"Observed control commands: {commands}")
print(f"Observed /state polls: {state_count}")
' "$events_file" play pause toggle_play_pause next previous set_volume seek set_delay
}

assert_kodi_logs() {
  local kodi_log="$RUNTIME_DIR/kodi.log"
  podman exec "$PODMAN_KODI_CONTAINER" sh -c "cat /config/.kodi/temp/kodi.log" >"$kodi_log"

  grep -F "Track changed to: Scenario Artist - Scenario Track (Scenario Album)" "$kodi_log" >/dev/null
  grep -F "Track changed to: Scenario Artist - Paused Scenario (Scenario Album)" "$kodi_log" >/dev/null
  if grep -E "\\[Sendspin\\].*(Fatal Startup Error|Async loop encountered an error)" "$kodi_log" >/dev/null; then
    echo "Unexpected Sendspin fatal error in Kodi log" >&2
    grep -E "\\[Sendspin\\].*(Fatal Startup Error|Async loop encountered an error)" "$kodi_log" >&2
    return 1
  fi
}

show_diagnostics() {
  echo
  echo "Kodi scenario log lines:"
  podman exec "$PODMAN_KODI_CONTAINER" sh -c \
    "test -f /config/.kodi/temp/kodi.log && grep -Ei 'sendspin|scenario|error|exception' /config/.kodi/temp/kodi.log | tail -n 160 || true"

  echo
  echo "Mock control API log lines:"
  podman logs --tail=120 "$PODMAN_MOCK_CONTAINER" || true
}

start_harness
run_state_scenarios
run_plugin_command_scenarios
run_direct_control_command_scenarios
run_delay_setting_scenario
assert_events
assert_kodi_logs
show_diagnostics
