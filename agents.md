# Agent Test & Verification Guide

Quick reference of what commands to run and when after making code changes.

---

## 1. Always Run (After Any Code Change)

```bash
# Lint and format
ruff check --fix .
ruff format .

# Pre-commit checks
pre-commit run --all-files

# Unit tests
python3 -m unittest discover -s tests
```

---

## 2. When Modifying Kodi Service, Routes, or Control API

Run headless Kodi container smoke tests:

```bash
# Basic startup & service smoke test
tests/kodi/smoke.sh
podman pod rm -f sendspin-kodi-test   # or: docker compose -f tests/kodi/docker-compose.yml down

# API scenario tests
tests/kodi/api_scenarios.sh
podman pod rm -f sendspin-kodi-test   # or: docker compose -f tests/kodi/docker-compose.yml down
```

---

## 3. When Changing Dependencies (`pyproject.toml`)

Re-sync vendored packages to `service.sendspin/resources/lib`:

```bash
python3 scripts/get_libs.py
```

---

## 4. When Modifying Docker Image Settings or Version

When editing `service.sendspin/docker_image_version.txt` or Docker settings in `service.sendspin/resources/settings.xml`:

```bash
tests/docker_image_pull_start_test.sh
```

---

## 5. Before Release or Version Bumps

Verify CalVer versioning, metadata sync (`addon.xml` vs `pyproject.toml`), and ZIP packaging:

```bash
python3 scripts/release.py --check
```

---

## 6. Target Hardware Deployment (LibreELEC / CoreELEC)

When deploying to a physical test device:

```bash
KODI_IP=<device-ip> sh upload
```
