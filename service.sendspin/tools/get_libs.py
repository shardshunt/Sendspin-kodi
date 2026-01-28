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


def run(cmd: list[str]) -> None:
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> int:
    # Path setup
    tools_dir = Path(__file__).resolve().parent
    addon_dir = tools_dir.parent
    project_root = addon_dir.parent
    target_dir = project_root / "service.sendspin" / "resources" / "lib"
    venv_dir = project_root / ".venv"

    # Define the pip path inside the venv
    pip_exe = venv_dir / "bin" / "pip"

    # 1. Sync dependencies from pyproject.toml
    # This ensures aiohttp, av, numpy, etc. are present in .venv
    run(["uv", "sync"])

    if not pip_exe.exists():
        print("Pip not found in venv. Installing pip via uv...")
        run(["uv", "pip", "install", "pip"])

    # 2. Install aiosendspin specifically into the venv
    # uv pip interacts directly with the .venv created by uv sync
    run([str(pip_exe), "install", "aiosendspin==3.0.0", "--no-deps", "--ignore-requires-python"])

    # 3. Clean and prepare target directory
    print(f"Cleaning target: {target_dir}")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 4. Locate site-packages
    # Standard location for Python 3.11 on Unix-like systems
    site_packages = venv_dir / "lib" / "python3.11" / "site-packages"

    # Fallback for Windows-style venv structure
    if not site_packages.exists():
        site_packages = venv_dir / "Lib" / "site-packages"

    if not site_packages.exists():
        print(f"Error: Could not find site-packages in {venv_dir}")
        return 1

    # 5. Copy packages to the Kodi addon directory
    print(f"Copying libraries to {target_dir}...")
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

        dest = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    print("Libraries installed to resources/lib.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
