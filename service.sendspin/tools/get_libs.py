#!/usr/bin/env python3
"""
Kodi vendor helper.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PACKAGES = [
    "aiohttp",
    "mashumaro",
    "zeroconf",
    "pillow",
    "orjson",
    "av==14.4.0",
]

AIOSENDSPIN_PKG = "aiosendspin==1.1.4"


def run(cmd: list[str]) -> None:
    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        sys.exit(proc.returncode)


def clean_target(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def enforce_cp311_only(target: Path) -> None:
    for so in target.rglob("*.so"):
        name = so.name
        if "cp314" in name or "cpython-314" in name:
            sys.exit(f"Incompatible binary detected: {name}")


def main() -> int:
    tools_dir = Path(__file__).resolve().parent
    addon_dir = tools_dir.parent
    target = addon_dir / "resources" / "lib"

    print("Cleaning target:", target)
    clean_target(target)

    base_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--only-binary=:all:",
        "--platform",
        "manylinux2014_x86_64",
        "--python-version",
        "311",
        "--implementation",
        "cp",
        "--abi",
        "cp311",
        "--target",
        str(target),
    ]

    # Install normal packages with dependencies
    for pkg in PACKAGES:
        run(base_cmd + [pkg])

    # Install aiosendspin itself only, ignoring Requires-Python
    run(
        base_cmd
        + [
            "--no-deps",
            "--ignore-requires-python",
            AIOSENDSPIN_PKG,
        ]
    )

    enforce_cp311_only(target)

    print("Vendor install complete. Final contents:")
    for p in sorted(target.iterdir()):
        print(" -", p.name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
