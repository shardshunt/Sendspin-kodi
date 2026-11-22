#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_STYLE_CHECKS=true

show_help() {
  cat <<'EOF'
Usage: scripts/full_test_suite.sh [--skip-style]

Run the deterministic pre-commit suite: Python unit tests, repository style
checks, Kodi API scenarios, and x86_64/i386 backend checks in Ubuntu and Fedora
containers. Hardware-dependent playback checks are not included.

Options:
  --skip-style  Skip repository-wide pre-commit checks (used by the commit hook,
                which has already run the staged-file checks).
  -h, --help    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-style)
      RUN_STYLE_CHECKS=false
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 2
      ;;
  esac
done

cd "$ROOT_DIR"

if [[ "$RUN_STYLE_CHECKS" == true ]]; then
  echo "=== Repository-wide pre-commit checks ==="
  SKIP="sendspin-full-suite${SKIP:+,$SKIP}" uv run pre-commit run --all-files
fi

echo "=== Python unit tests ==="
python3 -m unittest discover tests

echo "=== Kodi API scenarios ==="
tests/kodi/api_scenarios.sh

echo "=== Native backend distro smoke checks ==="
tests/kodi/distro_smoke.sh --skip-kodi

echo "Full deterministic test suite passed."
