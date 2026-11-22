#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f /etc/os-release ]]; then
  echo "Cannot identify container distribution" >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release

case "$ID" in
  ubuntu)
    dpkg --add-architecture i386
    apt-get update
    apt-get install --no-install-recommends -y \
      alsa-utils file python3 libasound2t64 libavahi-client3 \
      libasound2t64:i386 libavahi-client3:i386 libstdc++6:i386
    ;;
  fedora)
    dnf upgrade -y
    dnf install -y \
      alsa-utils file python3 \
      glibc.x86_64 glibc.i686 \
      libstdc++.x86_64 libstdc++.i686 \
      alsa-lib.x86_64 alsa-lib.i686 \
      avahi-libs.x86_64 avahi-libs.i686
    ;;
  *)
    echo "Unsupported smoke-test distribution: $ID" >&2
    exit 1
    ;;
esac

cat >/tmp/asound.conf <<'EOF'
pcm.!default {
    type null
}
ctl.!default {
    type null
}
EOF
export ALSA_CONFIG_PATH=/tmp/asound.conf

echo "Checking ALSA software null output on $PRETTY_NAME"
aplay -q -D default -t raw -f S16_LE -r 48000 -c 2 -d 1 /dev/zero

for arch in x86_64 i386; do
  backend="/backend/$arch/sendspin-kodi-backend"
  [[ -f "$backend" ]] || { echo "Missing $arch backend: $backend" >&2; exit 1; }
  echo "Checking $arch backend dependencies and startup"
  file "$backend"
  dependencies="$(ldd "$backend")"
  if grep -q 'not found' <<<"$dependencies"; then
    echo "$arch backend has unresolved libraries:" >&2
    echo "$dependencies" >&2
    exit 1
  fi
  "$backend" --version

  state_dir="/tmp/sendspin-kodi-$arch"
  mkdir -p "$state_dir"
  SENDSPIN_AUDIO_DEVICE=default "$backend" \
    --name "Sendspin CI $arch" \
    --state-file "$state_dir/state.json" \
    --control-socket "$state_dir/control.sock" \
    >"$state_dir/backend.log" 2>&1 &
  backend_pid=$!
  trap 'kill "$backend_pid" 2>/dev/null || true; wait "$backend_pid" 2>/dev/null || true' EXIT

  for _ in $(seq 1 100); do
    [[ -S "$state_dir/control.sock" ]] && break
    if ! kill -0 "$backend_pid" 2>/dev/null; then
      cat "$state_dir/backend.log" >&2
      echo "$arch backend exited before opening its control socket" >&2
      exit 1
    fi
    sleep 0.1
  done
  [[ -S "$state_dir/control.sock" ]] || {
    cat "$state_dir/backend.log" >&2
    echo "$arch backend did not open its control socket" >&2
    exit 1
  }

  python3 - "$state_dir/control.sock" <<'PY'
import json
import socket
import sys

connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.settimeout(3)
connection.connect(sys.argv[1])
connection.sendall(b'{"command":"get_state"}\n')
response = b""
while not response.endswith(b"\n"):
    chunk = connection.recv(4096)
    if not chunk:
        raise SystemExit("backend closed the control socket without a response")
    response += chunk
state = json.loads(response)
assert isinstance(state.get("connection"), dict), state
assert isinstance(state.get("audio"), dict), state
PY

  kill "$backend_pid"
  wait "$backend_pid"
  trap - EXIT
done
