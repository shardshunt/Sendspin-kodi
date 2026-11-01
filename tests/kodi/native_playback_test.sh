#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MA_HOST="${MA_HOST:-192.168.0.161}"
MA_API_URL="${MA_API_URL:-http://${MA_HOST}:8095/api}"
SENDSPIN_SERVER_URL="${SENDSPIN_SERVER_URL:-ws://${MA_HOST}:8927/sendspin}"
MA_QUERY="${MA_QUERY:-Middle Kids Beliefs and Prayers}"
MA_PLAYER_ID="${MA_PLAYER_ID:-}"
MA_PLAYER_NAME="${MA_PLAYER_NAME:-Kodi}"
MA_ALLOW_UNAVAILABLE="${MA_ALLOW_UNAVAILABLE:-false}"
MA_API_TOKEN="${MA_API_TOKEN:-}"
MA_API_TOKEN_FILE="${MA_API_TOKEN_FILE:-${HOME:-}/.config/sendspin-kodi/ma_api_token}"
ALSA_DEVICE="${ALSA_DEVICE:-}"
PODMAN_ROOTFUL="${PODMAN_ROOTFUL:-true}"
AUTO_DIAGNOSTICS="${AUTO_DIAGNOSTICS:-true}"
DIAGNOSTICS_TIMEOUT="${DIAGNOSTICS_TIMEOUT:-45}"
KODI_CONTAINER="${KODI_CONTAINER:-sendspin-kodi-native-test}"
HARNESS="$ROOT_DIR/tests/kodi/native_harness.sh"

die() { echo "native playback test: $*" >&2; exit 1; }
command -v curl >/dev/null || die "curl is required"
command -v python3 >/dev/null || die "python3 is required"
[[ -x "$HARNESS" ]] || die "native harness is missing"
if [[ -z "$ALSA_DEVICE" ]]; then
  ALSA_DEVICE="$(python3 "$ROOT_DIR/tests/kodi/resolve_host_alsa_device.py")" \
    || die "could not resolve the host's default ALSA output; set ALSA_DEVICE explicitly"
  echo "Using host default audio device: $ALSA_DEVICE"
fi
if [[ "$PODMAN_ROOTFUL" == true ]]; then
  sudo -v || die "sudo authentication is required for rootful Podman"
fi
if [[ -z "$MA_API_TOKEN" && -r "$MA_API_TOKEN_FILE" ]]; then
  MA_API_TOKEN="$(<"$MA_API_TOKEN_FILE")"
fi
[[ -n "$MA_API_TOKEN" ]] || die "set MA_API_TOKEN to a Music Assistant long-lived access token"

ma_call() {
  local command_name="$1" args_json="$2"
  curl -sS "$MA_API_URL" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $MA_API_TOKEN" \
    --data "$(python3 - "$command_name" "$args_json" <<'PY'
import json
import sys
print(json.dumps({"message_id": "sendspin-kodi-native-test", "command": sys.argv[1], "args": json.loads(sys.argv[2])}))
PY
)"
}

kodi_exec() {
  if [[ "$PODMAN_ROOTFUL" == true ]]; then
    sudo -n podman exec "$KODI_CONTAINER" "$@"
  else
    podman exec "$KODI_CONTAINER" "$@"
  fi
}

kodi_log_contains() {
  local pattern="$1"
  kodi_exec sh -c 'grep -E -- "$1" /config/.kodi/temp/kodi.log >/dev/null 2>&1' sh "$pattern"
}

wait_for_kodi_log() {
  local pattern="$1" timeout_seconds="$2"
  for _ in $(seq 1 "$timeout_seconds"); do
    if kodi_log_contains "$pattern"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

audio_diagnostics() {
  local alsa_card="${ALSA_DEVICE#hw:}"
  local alsa_device="${alsa_card#*,}"
  alsa_card="${alsa_card%%,*}"
  local pcm_node="/dev/snd/pcmC${alsa_card}D${alsa_device}p"
  echo
  echo "--- Native audio diagnostics ---"
  kodi_exec sh -c '
    echo "[PortAudio/backend]"
    grep -E "PortAudio:|failed to open|Could not configure|Acquired Sendspin|Stream successfully|Sendspin server connection" /config/.kodi/temp/kodi.log | tail -n 80 || true
    echo "[PCM status]"
    for status in /proc/asound/card*/pcm*/sub*/status; do
      state=$(head -n 1 "$status" 2>/dev/null || true)
      case "$state" in
        closed|"") ;;
        *) echo "$status"; cat "$status" ;;
      esac
    done
    echo "[backend state]"
    cat /config/.kodi/userdata/addon_data/service.sendspin/sendspin/state.json 2>/dev/null || true
  ' || true

  echo "[host device owner]"
  sudo -n fuser -v "$pcm_node" 2>&1 || true
  echo "[host mixer]"
  sudo -n amixer -c "$alsa_card" scontents 2>&1 || true
  echo "--- End native audio diagnostics ---"
}

HARNESS_PID=""
cleanup() {
  if [[ -n "$HARNESS_PID" ]] && kill -0 "$HARNESS_PID" 2>/dev/null; then
    kill -TERM "$HARNESS_PID" 2>/dev/null || true
    wait "$HARNESS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ALSA_DEVICE="$ALSA_DEVICE" SENDSPIN_SERVER_URL="$SENDSPIN_SERVER_URL" \
PODMAN_ROOTFUL="$PODMAN_ROOTFUL" KODI_PRIVILEGED="${KODI_PRIVILEGED:-true}" "$HARNESS" &
HARNESS_PID=$!

for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8080/jsonrpc -H 'Content-Type: application/json' \
      --data '{"jsonrpc":"2.0","method":"JSONRPC.Ping","id":1}' >/dev/null 2>&1; then
    break
  fi
  kill -0 "$HARNESS_PID" 2>/dev/null || die "native harness exited before Kodi became ready"
  sleep 2
done
curl -fsS http://127.0.0.1:8080/jsonrpc -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"JSONRPC.Ping","id":1}' >/dev/null \
  || die "Kodi did not become ready"

echo "Waiting for the native Sendspin backend..."
for _ in $(seq 1 60); do
  if [[ "$PODMAN_ROOTFUL" == true ]]; then
    if sudo -n podman exec "$KODI_CONTAINER" test -S \
      /config/.kodi/userdata/addon_data/service.sendspin/sendspin/control.sock 2>/dev/null; then
      break
    fi
  elif podman exec "$KODI_CONTAINER" test -S \
      /config/.kodi/userdata/addon_data/service.sendspin/sendspin/control.sock 2>/dev/null; then
    break
  fi
  kill -0 "$HARNESS_PID" 2>/dev/null || die "native harness exited before the backend became ready"
  sleep 1
done
if [[ "$PODMAN_ROOTFUL" == true ]]; then
  sudo -n podman exec "$KODI_CONTAINER" test -S \
    /config/.kodi/userdata/addon_data/service.sendspin/sendspin/control.sock 2>/dev/null \
    || die "native Sendspin backend did not become ready"
else
  podman exec "$KODI_CONTAINER" test -S \
    /config/.kodi/userdata/addon_data/service.sendspin/sendspin/control.sock 2>/dev/null \
    || die "native Sendspin backend did not become ready"
fi

if [[ -z "$MA_PLAYER_ID" ]]; then
  for _ in $(seq 1 30); do
    players_json="$(ma_call players/all '{}' 2>/dev/null || true)"
    MA_PLAYER_ID="$(python3 - "$players_json" "$MA_PLAYER_NAME" "$MA_ALLOW_UNAVAILABLE" <<'PY'
import json
import sys
try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError:
    raise SystemExit(1)
wanted = sys.argv[2].lower()
allow_unavailable = sys.argv[3].lower() == "true"
result = payload.get("result", payload) if isinstance(payload, dict) else payload
players = result.get("players", result.get("items", [])) if isinstance(result, dict) else result
players = players if isinstance(players, list) else []
for player in players:
    name = str(player.get("name", ""))
    provider = str(player.get("provider") or "")
    player_id = player.get("player_id") or player.get("id")
    available = player.get("available", False)
    if player_id and wanted in name.lower() and (available or allow_unavailable):
        print(player_id)
        print(f"Using MA player: {name} [{player_id}] ({provider}, available={available})", file=sys.stderr)
        break
else:
    raise SystemExit(1)
PY
    )" || MA_PLAYER_ID=""
    [[ -n "$MA_PLAYER_ID" ]] && break
    sleep 2
  done
  [[ -n "$MA_PLAYER_ID" ]] || die "could not find a Sendspin player named '$MA_PLAYER_NAME'"
fi

search_args="$(python3 - "$MA_QUERY" <<'PY'
import json
import sys
print(json.dumps({"search_query": sys.argv[1], "media_types": ["track"], "limit": 10}))
PY
)"
search_json="$(ma_call music/search "$search_args")" || die "Music Assistant search failed"
media_uri="$(python3 - "$search_json" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
result = payload.get("result", payload) if isinstance(payload, dict) else payload
for track in result.get("tracks", []) if isinstance(result, dict) else []:
    uri = track.get("uri") or track.get("media_item", {}).get("uri")
    if uri:
        print(uri)
        print(f"Playing: {track.get('name', uri)}", file=sys.stderr)
        break
else:
    raise SystemExit("no playable track returned")
PY
)" || die "no playable Music Assistant track found"

play_args="$(python3 - "$MA_PLAYER_ID" "$media_uri" <<'PY'
import json
import sys
print(json.dumps({"queue_id": sys.argv[1], "media": [sys.argv[2]]}))
PY
)"
play_response="$(ma_call player_queues/play_media "$play_args" || true)"
if ! python3 - "$play_response" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if isinstance(payload, dict) and "error" in payload:
    raise SystemExit(1)
if isinstance(payload, dict) and payload.get("result") is False:
    raise SystemExit(1)
PY
then
  echo "Music Assistant playback response:" >&2
  echo "$play_response" >&2
  die "Music Assistant could not start playback"
fi

echo "Native playback test is running: $MA_QUERY"
echo "Press Ctrl-C to stop."
if [[ "$AUTO_DIAGNOSTICS" == true ]]; then
  echo "Waiting for native playback before collecting diagnostics..."
  if wait_for_kodi_log "Acquired Sendspin audio output" "$DIAGNOSTICS_TIMEOUT"; then
    # Give the backend a short opportunity to open the stream and begin
    # consuming audio before taking the snapshot.
    sleep 2
    audio_diagnostics
  else
    echo "Native playback did not reach the audio-acquired milestone within ${DIAGNOSTICS_TIMEOUT}s."
    audio_diagnostics
  fi
fi
if [[ "$PODMAN_ROOTFUL" == true ]]; then
  sudo -n podman exec "$KODI_CONTAINER" sh -c \
    'tail -f /config/.kodi/temp/kodi.log | grep --line-buffered -Ei "Sendspin|connected|playing|track|stream|error"'
else
  podman exec "$KODI_CONTAINER" sh -c \
    'tail -f /config/.kodi/temp/kodi.log | grep --line-buffered -Ei "Sendspin|connected|playing|track|stream|error"'
fi
wait "$HARNESS_PID"
