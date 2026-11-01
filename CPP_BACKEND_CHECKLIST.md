# Sendspin C++ Backend Checklist

Branch: `consider-senspin-cpp-instead-of-docker`

## Scope

- [ ] Replace the Docker-hosted playback backend with an integration that uses `sendspin-cpp`.
- [ ] Build the new integration cleanly around `sendspin-cpp`; do not preserve Docker as a fallback.
- [ ] Treat `sendspin-cpp` as an external dependency: do not modify or reimplement its library functionality.
- [ ] Confirm supported distributions, CPU architectures, and minimum versions for LibreELEC, Debian, and Fedora. LibreELEC Generic x86_64 12.2.1 is the only physical target verified so far.
- [x] Define expected behavior when the backend cannot be built or started.

## Design and Feasibility

- [x] Complete [the architecture notes](CPP_BACKEND_ARCHITECTURE.md), focusing on the new components, interfaces, and lifecycle.
- [x] Identify the minimum Kodi service behavior and configuration that should remain after removing Docker.
- [x] Verify `sendspin-cpp` supports the required Sendspin connection, audio output, state reporting, and controls without library changes.
- [x] Verify how the C++ project is built, versioned, distributed, and consumed; record its public integration interface.
- [x] Decide how the C++ dependency is pinned and built for the target systems (`sendspin-cpp` v0.9.5, Ubuntu 22.04 build container, x86_64/aarch64/armv7 build matrix).
- [x] Define how Kodi communicates with the backend and how state, errors, and logs reach the add-on.
- [x] Define audio-device selection, release/reacquire, startup readiness, shutdown, and recovery behavior.
- [x] Define timing/synchronization and two-way volume/mute behavior, including units and ownership.
- [ ] Record intentional breaking changes; no migration or rollback for released Docker installations is required for this branch.

## Implementation

### Phase 1: Strip Back to Basics

- [x] Remove Docker-specific process management and container/image configuration from the add-on.
- [x] Remove Docker-specific settings, startup paths, and obsolete deployment/package references.
- [x] Remove or replace tests that only exercise the Docker implementation.
- [x] Reduce the Kodi service to the smallest useful structure for integrating and controlling the new backend.
- [x] Record intentional breaking changes; no migration or rollback for released Docker installations is required for this branch.

### Phase 2: Integrate the C++ Backend

- [x] Build the `sendspin-cpp` dependency for one target system using its supported public interface.
- [x] Add the smallest Kodi-side integration needed to launch, configure, and stop the backend.
- [x] Confirm the backend can select the intended system audio device and play a Sendspin stream on LibreELEC Generic x86_64 12.2.1.
- [x] Implement and physically verify audio-device selection and audio handoff between Kodi and the backend on LibreELEC Generic x86_64 12.2.1.
- [x] Implement playback timing and synchronization.
- [x] Implement two-way volume and mute synchronization.
- [x] Implement Kodi metadata, player-state, and UI synchronization.
- [x] Handle backend startup failure, process exit, Kodi wake, and audio-device changes.
- [x] Add only the settings and persisted configuration needed by the new implementation.

### Phase 3: Complete Target Support

- [ ] Build and run the new integration on LibreELEC, Debian, and Fedora targets in scope.
- [ ] Remove any temporary scaffolding that is no longer needed.
- [x] Update project documentation and test/deployment scripts to describe the new implementation.

## Validation

- [x] Add unit tests for backend lifecycle, configuration, readiness, and failure handling.
- [x] Add integration coverage for the backend interface and playback/control state synchronization (`tests/kodi/native_playback_test.sh`).
- [ ] Test startup, playback, pause/resume, stop/idle, shutdown, and unexpected backend exit for the new implementation.
- [x] Test audio-device selection and release/reacquire while Kodi and Sendspin contend for audio on LibreELEC Generic x86_64 12.2.1.
- [ ] Verify Kodi remains on HDMI4 while Sendspin is idle, routes Sendspin playback to HDMI4, and restores Kodi after pause. Idle-time handoff is removed; HDMI label matching is fixed. Set override `hw:1,10` to pin this box to HDMI4; device retest pending.
- [x] Verify playback, volume updates, metadata updates, and Kodi player/UI state on the LibreELEC test device.
- [x] Verify Kodi volume and mute controls on the LibreELEC test device.
- [x] Verify Kodi-originated pause forwarding from KORE on LibreELEC Generic x86_64 12.2.1.
- [ ] Measure playback synchronization/timing on target hardware.
- [ ] Test clear failure behavior when the backend is missing, incompatible, or fails to start.
- [ ] Run `ruff check --fix .` and `ruff format .` across the repository.
- [x] Pass Ruff and Ruff-format commit hooks for the changed Python files.
- [ ] Run `pre-commit run --all-files`.
- [x] Run focused playback unit tests: `python3 -m unittest discover -s tests -p test_audio_playback.py` (12 tests).
- [x] Run `python3 -m unittest discover -s tests` (14 tests, passed 2026-11-01).
- [ ] Update and run applicable Kodi smoke/API tests for the new implementation; do not require Docker for these tests.
- [ ] Perform and record physical-device testing for LibreELEC and representative Debian/Fedora systems.
- [x] Record tested OS versions, CPU architectures, dependency versions, results, and known limitations.

### Recorded Device Results

- LibreELEC Generic x86_64 12.2.1, Kodi Python 3.11.13 (2026-11-01): backend started, connected to Music Assistant, selected `hw:1,10` (HDA ATI HDMI / Panasonic-TV), and playback plus Kodi audio release/reacquire were observed. Kodi volume/mute, track metadata updates, and KORE pause forwarding worked. First-play metadata refresh was also confirmed by the user.
- Kodi pause forwarding uses the polled Kodi paused-state transition, preserves a pending pause for up to five seconds, and reports backend rejection. Unit and LibreELEC device testing passed.
- The 2026-11-01 logs exposed two routing issues: restoring the captured PulseAudio Bluetooth device, then treating Kodi's HDMI `DEV=0` as ALSA PCM 0. Restore now follows the target when the hardware override is set, and HDMI labels take precedence in ALSA mapping. Unit tests pass; HDMI4 device verification is pending with override `hw:1,10`.
- A later idle-start log showed Kodi switching to Bluetooth before Sendspin playback began. Setup now leaves Kodi routing unchanged until the first active stream; unit tests pass, and on-device idle/play/pause verification is pending.
- ELF inspection of the packaged backends showed requirements through `GLIBC_2.34` and `GLIBCXX_3.4.30`. The LibreELEC device above runs the x86_64 binary; aarch64/armv7 runtime compatibility and Debian/Fedora minimum versions remain unverified.
- The native playback harness uses live Music Assistant and ALSA hardware. It is an integration test, but its hardware/device-specific result does not replace the remaining OS/architecture and lifecycle validation.

### Deferred

- KORE's native Next button mapping to Sendspin is deferred. KORE sends Kodi's `Player.GoTo(NEXT)` and has no custom button-action mapping in its preferences. The add-on's existing Next plugin/API command remains available to other callers.

## Completion Criteria

- [ ] Docker-specific code and configuration have been removed from the add-on and its active test/deployment paths.
- [ ] The new backend builds and runs on every agreed target.
- [ ] Required playback, audio handoff, synchronization, volume, and Kodi UI behavior passes validation.
- [ ] The integration uses the external `sendspin-cpp` dependency without modifying or duplicating its library implementation.
- [ ] Automated checks and platform-specific validation pass, with exceptions documented.
- [ ] Documentation describes the new dependency, build/run requirements, and known limitations.
