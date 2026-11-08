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

## 4. When Building the Native Backend

Build all supported architectures before GitHub publishing, without installing
the toolchain on the host:

```bash
scripts/build_native_backends_all.sh
```

For development-only builds or target-specific testing, select an architecture:

```bash
NATIVE_ARCHES=x86_64 scripts/build_native_backends_all.sh
```

Selected builds do not create the full-matrix release record. `scripts/release.py`
requires a current record and matching binaries; even `--force` cannot bypass it.

---

## 5. Before Release or Version Bumps

Run the full backend matrix first, then verify CalVer versioning, metadata sync
(`addon.xml` vs `pyproject.toml`), the matrix record, and ZIP packaging:

```bash
scripts/build_native_backends_all.sh
python3 scripts/release.py --check
```

---

## 6. Target Hardware Deployment (LibreELEC / CoreELEC)

When deploying to a physical test device:

```bash
KODI_IP=<device-ip> sh upload
```
