#!/bin/sh
# place in /storage/.config/pulseaudio-fix/wait-for-pulse-sink.sh
# Wait until PulseAudio has a real sink (exclude any sink whose name contains "null", case-insensitive).
LOG="/storage/.config/pulseaudio-fix/wait-for-pulse-sink.log"
MAX_WAIT=${MAX_WAIT:-60}
SINK_NAME="${SINK_NAME:-}"

echo "$(date -Iseconds) wait-for-pulse-sink starting (max ${MAX_WAIT}s) SINK_NAME='${SINK_NAME}'" >>"$LOG"

# Ensure module-udev-detect is loaded (harmless if already)
if ! pactl list short modules 2>/dev/null | awk '{print $2}' | grep -q '^module-udev-detect$'; then
  echo "$(date -Iseconds) loading module-udev-detect" >>"$LOG"
  pactl load-module module-udev-detect tsched=0 >>"$LOG" 2>&1 || true
fi

i=0
while [ $i -lt "$MAX_WAIT" ]; do
  # If a specific sink name requested, check for its presence (exact match)
  if [ -n "$SINK_NAME" ]; then
    if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -xq "$SINK_NAME"; then
      echo "$(date -Iseconds) requested sink '$SINK_NAME' present" >>"$LOG"
      exit 0
    fi
  else
    # Look for any sink whose name does NOT contain "null" (case-insensitive)
    REAL_SINK=$(pactl list short sinks 2>/dev/null | awk 'BEGIN{IGNORECASE=1} $2 !~ /null/ {print $2; exit}')
    if [ -n "$REAL_SINK" ]; then
      echo "$(date -Iseconds) real sink found: $REAL_SINK" >>"$LOG"
      pactl list short sinks >>"$LOG" 2>&1 || true
      exit 0
    fi
  fi

  sleep 1
  i=$((i+1))
done

echo "$(date -Iseconds) timeout waiting for real sink after $MAX_WAIT seconds" >>"$LOG"
pactl list short sinks >>"$LOG" 2>&1 || true
exit 1
