#!/usr/bin/env python3
"""Resolve the host's current default audio sink to its ALSA playback device."""

import json
import shutil
import subprocess
import sys


def pactl(*arguments: str) -> str:
    result = subprocess.run(["pactl", *arguments], capture_output=True, check=True, text=True, timeout=5)
    return result.stdout.strip()


def main() -> int:
    if shutil.which("pactl") is None:
        print("pactl is required to detect the host's default audio sink", file=sys.stderr)
        return 1

    try:
        default_name = pactl("get-default-sink")
        sinks = json.loads(pactl("-f", "json", "list", "sinks"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"could not query the host's default audio sink: {error}", file=sys.stderr)
        return 1

    sink = next((item for item in sinks if item.get("name") == default_name), None)
    if sink is None:
        print(f"host default sink {default_name!r} was not found", file=sys.stderr)
        return 1

    properties = sink.get("properties", {})
    card = properties.get("alsa.card") or properties.get("api.alsa.pcm.card")
    device = properties.get("alsa.device") or properties.get("api.alsa.pcm.device")
    if not all(value is not None and str(value).isdecimal() for value in (card, device)):
        print(
            f"host default sink {default_name!r} is not a directly addressable ALSA output; "
            "set ALSA_DEVICE=hw:CARD,DEVICE explicitly",
            file=sys.stderr,
        )
        return 1

    alsa_device = f"hw:{card},{device}"
    print(alsa_device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
