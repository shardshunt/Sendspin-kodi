#!/bin/sh

# usage:
# ./reset_audio_device root@KodiIP

HOST="$1"

if [ -z "$HOST" ]; then
  echo "Usage: $0 user@host"
  exit 1
fi

ssh "$HOST" <<'EOF'
systemctl stop kodi
pactl unload-module module-udev-detect > /dev/null 2>&1
pactl load-module module-udev-detect
systemctl start kodi
EOF