#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="${RUNTIME_DIR:-$ROOT_DIR/tests/kodi/.native-runtime}"
RUNTIME_ADDON_DIR="$RUNTIME_DIR/addons/service.sendspin"
RUNTIME_USERDATA_DIR="$RUNTIME_DIR/userdata"
if [[ -n "${NATIVE_BACKEND:-}" ]]; then
  BACKEND_SOURCE="$NATIVE_BACKEND"
else
  case "$(uname -m)" in
    x86_64|amd64) native_arch=x86_64 ;;
    aarch64|arm64) native_arch=aarch64 ;;
    armv7|armv7l|armhf) native_arch=armv7 ;;
    *) native_arch="$(uname -m)" ;;
  esac
  BACKEND_SOURCE="$ROOT_DIR/service.sendspin/resources/bin/$native_arch/sendspin-kodi-backend"
  [[ -x "$BACKEND_SOURCE" ]] || BACKEND_SOURCE="$ROOT_DIR/service.sendspin/resources/bin/sendspin-kodi-backend"
fi
KODI_IMAGE="${KODI_IMAGE:-localhost/sendspin-kodi-native:omega-pactl}"
KODI_CONTAINERFILE="${KODI_CONTAINERFILE:-$ROOT_DIR/tests/kodi/Containerfile.native-kodi}"
KODI_CONTAINER="${KODI_CONTAINER:-sendspin-kodi-native-test}"
MA_CONTAINER="${MA_CONTAINER:-sendspin-music-assistant-test}"
MA_IMAGE="${MA_IMAGE:-ghcr.io/music-assistant/server:latest}"
MA_DATA_DIR="${MA_DATA_DIR:-$RUNTIME_DIR/music-assistant-data}"
SENDSPIN_SERVER_URL="${SENDSPIN_SERVER_URL:-}"
START_MUSIC_ASSISTANT="${START_MUSIC_ASSISTANT:-false}"
ALSA_DEVICE="${ALSA_DEVICE:-}"
KODI_PRIVILEGED="${KODI_PRIVILEGED:-false}"
PODMAN_ROOTFUL="${PODMAN_ROOTFUL:-false}"
ORIGINAL_DEFAULT_SINK=""
if command -v pactl >/dev/null 2>&1; then
  ORIGINAL_DEFAULT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
fi

die() { echo "native harness: $*" >&2; exit 1; }
podman_cmd=(podman)
if [[ "$PODMAN_ROOTFUL" == true ]]; then
  sudo -v || die "sudo authentication is required for rootful Podman"
  podman_cmd=(sudo -n podman)
fi
p() { "${podman_cmd[@]}" "$@"; }

restore_host_audio() {
  command -v pactl >/dev/null 2>&1 || return 0

  local usb_card usb_sink target_sink
  usb_card="$(pactl list short cards 2>/dev/null | awk '/C-Media.*USB_Audio_Device/ {print $2; exit}')"
  if [[ -n "$usb_card" ]]; then
    # A forced backend/container stop can leave the card registered but with
    # no playback node. Cycling the profile recreates the PipeWire sink.
    pactl set-card-profile "$usb_card" off 2>/dev/null || true
    sleep 1
    if ! pactl set-card-profile "$usb_card" output:analog-stereo+input:mono-fallback 2>/dev/null; then
      pactl set-card-profile "$usb_card" output:analog-stereo 2>/dev/null || true
    fi
  fi

  for _ in 1 2 3 4 5; do
    sleep 1
    usb_sink="$(pactl list short sinks 2>/dev/null | awk '/C-Media.*USB_Audio_Device.*analog-stereo/ {print $2; exit}')"
    [[ -n "$usb_sink" ]] && break
  done
  if [[ -n "$usb_sink" ]]; then
    pactl suspend-sink "$usb_sink" 0 2>/dev/null || true
  fi

  target_sink="$ORIGINAL_DEFAULT_SINK"
  if [[ -z "$target_sink" ]] || ! pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fxq "$target_sink"; then
    target_sink="$usb_sink"
  fi
  [[ -n "$target_sink" ]] && pactl set-default-sink "$target_sink" 2>/dev/null || true
}
cleanup() {
  p rm -f "$KODI_CONTAINER" >/dev/null 2>&1 || true
  [[ "$START_MUSIC_ASSISTANT" == true ]] && p rm -f "$MA_CONTAINER" >/dev/null 2>&1 || true
  restore_host_audio
}
cleanup_runtime() {
  [[ ! -e "$RUNTIME_DIR" ]] && return 0
  if rm -rf "$RUNTIME_DIR" 2>/dev/null; then
    return 0
  fi
  if [[ "$PODMAN_ROOTFUL" == true ]]; then
    sudo -n rm -rf -- "$RUNTIME_DIR"
  else
    p unshare rm -rf "$RUNTIME_DIR"
  fi
}
diagnostics() {
  if p container exists "$KODI_CONTAINER"; then
    p inspect "$KODI_CONTAINER" --format 'Kodi container: status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' || true
    p logs --tail=120 "$KODI_CONTAINER" || true
    p exec "$KODI_CONTAINER" sh -c "test -f /config/.kodi/temp/kodi.log && grep -Ei 'sendspin|service.sendspin|error|exception' /config/.kodi/temp/kodi.log | tail -n 180 || true" || true
  fi
  if [[ "$START_MUSIC_ASSISTANT" == true ]] && p container exists "$MA_CONTAINER"; then
    p logs --tail=120 "$MA_CONTAINER" || true
  fi
}

command -v podman >/dev/null 2>&1 || die "Podman is required"
[[ -x "$BACKEND_SOURCE" ]] || die "native backend is missing or not executable: $BACKEND_SOURCE"
[[ -f "$KODI_CONTAINERFILE" ]] || die "Kodi container file is missing: $KODI_CONTAINERFILE"
if [[ -z "$SENDSPIN_SERVER_URL" ]]; then
  [[ "$START_MUSIC_ASSISTANT" == true ]] && SENDSPIN_SERVER_URL="ws://127.0.0.1:8927/sendspin" || die "set SENDSPIN_SERVER_URL"
fi
if [[ -z "$ALSA_DEVICE" ]]; then
  ALSA_DEVICE="$(python3 "$ROOT_DIR/tests/kodi/resolve_host_alsa_device.py")" \
    || die "could not resolve the host's default ALSA output; set ALSA_DEVICE explicitly"
  echo "Using host default audio device: $ALSA_DEVICE"
fi
[[ -d /dev/snd ]] || die "No /dev/snd on the host; provide an ALSA device or loopback device before running the full playback harness"

trap 'diagnostics; cleanup' EXIT
cleanup
cleanup_runtime
mkdir -p "$RUNTIME_ADDON_DIR/resources/bin" "$RUNTIME_USERDATA_DIR/addon_data/service.sendspin" "$MA_DATA_DIR"
cp -a "$ROOT_DIR/service.sendspin/." "$RUNTIME_ADDON_DIR/"
cp -a "$ROOT_DIR/tests/kodi/userdata-podman/." "$RUNTIME_USERDATA_DIR/"
install -m 0755 "$BACKEND_SOURCE" "$RUNTIME_ADDON_DIR/resources/bin/sendspin-kodi-backend"
python3 - "$RUNTIME_DIR/asound.conf" "$ALSA_DEVICE" <<'PY'
import re
import sys

path, device = sys.argv[1:]
match = re.fullmatch(r"hw:(\d+),(\d+)", device)
if match is None:
    raise SystemExit("ALSA_DEVICE must have the form hw:CARD,DEVICE")
card, _device = match.groups()
with open(path, "w", encoding="utf-8") as output:
    output.write(
        f'pcm.!default {{ type plug; slave.pcm "{device}"; }}\n'
    )
PY

python3 - "$RUNTIME_USERDATA_DIR/addon_data/service.sendspin/settings.xml" "$SENDSPIN_SERVER_URL" <<'PY'
import sys
import xml.etree.ElementTree as ET
path, server_url = sys.argv[1:]
tree = ET.parse(path)
root = tree.getroot()
for setting_id, value in {"server_url": server_url, "backend_path": "", "control_url": "http://127.0.0.1:59999", "audio_device_override": ""}.items():
    setting = root.find(f"setting[@id='{setting_id}']")
    if setting is None:
        setting = ET.SubElement(root, "setting", {"id": setting_id})
    setting.text = value
tree.write(path, encoding="utf-8", xml_declaration=False)
PY

if [[ "$START_MUSIC_ASSISTANT" == true ]]; then
  p run -d --name "$MA_CONTAINER" --network host -v "$MA_DATA_DIR:/data:Z" "$MA_IMAGE" >/dev/null
  for _ in $(seq 1 90); do if curl -fsS http://127.0.0.1:8095/ >/dev/null 2>&1; then break; fi; sleep 2; done
  curl -fsS http://127.0.0.1:8095/ >/dev/null || die "Music Assistant did not become ready"
fi
if ! p image exists "$KODI_IMAGE"; then p build -t "$KODI_IMAGE" -f "$KODI_CONTAINERFILE" "$ROOT_DIR/tests/kodi"; fi

device_args=()
[[ -d /dev/snd ]] && device_args+=(--device /dev/snd:/dev/snd)
audio_metadata_args=()
[[ -d /proc/asound ]] && audio_metadata_args+=(--volume /proc/asound:/proc/asound:ro)
[[ -d /sys/class/sound ]] && audio_metadata_args+=(--volume /sys/class/sound:/sys/class/sound:ro)
security_args=(--security-opt label=disable)
[[ "$KODI_PRIVILEGED" == true ]] && security_args+=(--privileged)
audio_group_args=()
audio_gid=""
if [[ -d /dev/snd ]]; then
  alsa_card="${ALSA_DEVICE#hw:}"
  alsa_card="${alsa_card%%,*}"
  alsa_device="${ALSA_DEVICE##*,}"
  audio_gid="$(stat -c '%g' "/dev/snd/pcmC${alsa_card}D${alsa_device}p" 2>/dev/null || true)"
fi
if [[ -n "$audio_gid" ]]; then
  audio_group_args+=(--group-add "$audio_gid")
else
  audio_group_args+=(--group-add keep-groups)
fi
pipewire_args=()
host_runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [[ -S "$host_runtime_dir/pulse/native" ]]; then
  pipewire_args+=(
    --volume "$host_runtime_dir:$host_runtime_dir:rw"
    -e "XDG_RUNTIME_DIR=$host_runtime_dir"
    -e "PULSE_SERVER=unix:$host_runtime_dir/pulse/native"
  )
fi
p run -d --name "$KODI_CONTAINER" --network host --userns host \
  "${audio_group_args[@]}" \
  "${security_args[@]}" "${device_args[@]}" "${audio_metadata_args[@]}" \
  -e "PUID=${PUID:-$(id -u)}" -e "PGID=${PGID:-${audio_gid:-$(id -g "${USER:-$(id -un)}")}}" -e "TZ=${TZ:-Pacific/Auckland}" \
  -e PA_ALSA_PLUGHW=1 \
  -e "SENDSPIN_AUDIO_DEVICE=${ALSA_DEVICE}" \
  "${pipewire_args[@]}" \
  -v "$RUNTIME_DIR/asound.conf:/etc/asound.conf:ro" \
  -v "$RUNTIME_USERDATA_DIR:/config/.kodi/userdata:Z" \
  -v "$RUNTIME_ADDON_DIR:/config/.kodi/addons/service.sendspin:Z" "$KODI_IMAGE" >/dev/null

JSONRPC_URL="http://127.0.0.1:8080/jsonrpc"
for _ in $(seq 1 60); do
  if curl -fsS "$JSONRPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"JSONRPC.Ping","id":1}' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "$JSONRPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"Addons.SetAddonEnabled","params":{"addonid":"service.sendspin","enabled":true},"id":2}'
echo
echo "Native harness running; Sendspin URL: $SENDSPIN_SERVER_URL"
echo "Kodi JSON-RPC: $JSONRPC_URL"
while :; do
  kodi_running="$(p inspect -f '{{.State.Running}}' "$KODI_CONTAINER" 2>/dev/null || true)"
  if [[ "$kodi_running" == true ]]; then
    sleep 5
    continue
  fi

  # Rootful Podman can briefly reject inspect while the container is being
  # started or its init process is changing state. Do not mistake that
  # transient empty result for a stopped Kodi container.
  if [[ -z "$kodi_running" ]] && p container exists "$KODI_CONTAINER"; then
    sleep 2
    continue
  fi
  die "Kodi container exited"
done
