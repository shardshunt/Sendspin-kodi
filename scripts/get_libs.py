#!/usr/bin/env python3
import shutil
import subprocess
import sys
from pathlib import Path

# Packages to explicitly exclude from the vendor folder
DEV_PACKAGES = [
    "kodistubs",
    "pre-commit",
    "pre_commit",
    "ruff",
    "nodeenv",
    "yaml",
    "_pytest",
    "pytest",
]

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "plugin.audio.sendspin"
TARGET_DIR = ADDON_DIR / "resources" / "lib"
VENV_DIR = ROOT / ".venv"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=cwd)


def main() -> int:
    # 1. Sync dependencies from pyproject.toml
    # This ensures any project dependencies are present in .venv
    try:
        run(["uv", "sync"], cwd=ROOT)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Error: uv sync failed: {exc}")
        return 1

    # Define the pip path inside the venv
    pip_exe = VENV_DIR / "bin" / "pip"
    if not pip_exe.exists():
        # Fallback for Windows-style venv structure
        pip_exe = VENV_DIR / "Scripts" / "pip.exe"

    if not pip_exe.exists():
        print("Pip not found in venv. Installing pip via uv...")
        run(["uv", "pip", "install", "pip"], cwd=ROOT)

    # 2. Clean and prepare target directory
    print(f"Cleaning target: {TARGET_DIR}")
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Locate site-packages
    # Standard location for Python 3.11 on Unix-like systems
    site_packages = VENV_DIR / "lib" / "python3.11" / "site-packages"

    # Fallback for Windows-style venv structure
    if not site_packages.exists():
        site_packages = VENV_DIR / "Lib" / "site-packages"

    if not site_packages.exists():
        print(f"Error: Could not find site-packages in {VENV_DIR}")
        return 1

    # 4. Copy packages to the Kodi addon directory
    print(f"Copying libraries to {TARGET_DIR}...")
    for item in site_packages.iterdir():
        # Skip metadata, installer tools, and cache files
        if item.name.endswith((".dist-info", ".pth", ".pyc")) or item.name in [
            "__pycache__",
            "pip",
            "_pytest",
            "setuptools",
        ]:
            continue
        # Skip dev packages
        if item.name.lower() in [p.lower() for p in DEV_PACKAGES]:
            print(f" - Skipping dev dependency: {item.name}")
            continue

        dest = TARGET_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    print("Libraries installed to resources/lib.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
