# Sendspin C++ Backend Checklist

Branch: `consider-senspin-cpp-instead-of-docker`

## Scope

- [ ] Replace the Docker-hosted playback backend with an integration that uses `sendspin-cpp`.
- [ ] Build the new integration cleanly around `sendspin-cpp`; do not preserve Docker as a fallback.
- [ ] Treat `sendspin-cpp` as an external dependency: do not modify or reimplement its library functionality.
- [ ] Confirm supported distributions, CPU architectures, and minimum versions for LibreELEC, Debian, and Fedora.
- [x] Define expected behavior when the backend cannot be built or started.

## Design and Feasibility

- [x] Complete [the architecture notes](CPP_BACKEND_ARCHITECTURE.md), focusing on the new components, interfaces, and lifecycle.
- [x] Identify the minimum Kodi service behavior and configuration that should remain after removing Docker.
- [x] Verify `sendspin-cpp` supports the required Sendspin connection, audio output, state reporting, and controls without library changes.
- [x] Verify how the C++ project is built, versioned, distributed, and consumed; record its public integration interface.
- [ ] Decide how the C++ dependency is pinned and built for the target systems.
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
- [ ] Confirm the backend can select the intended system audio device and play a Sendspin stream.
- [ ] Implement audio-device selection and audio handoff between Kodi and the backend.
- [x] Implement playback timing and synchronization.
- [x] Implement two-way volume and mute synchronization.
- [x] Implement Kodi metadata, player-state, and UI synchronization.
- [x] Handle backend startup failure, process exit, Kodi wake, and audio-device changes.
- [x] Add only the settings and persisted configuration needed by the new implementation.

### Phase 3: Complete Target Support

- [ ] Build and run the new integration on LibreELEC, Debian, and Fedora targets in scope.
- [ ] Remove any temporary scaffolding that is no longer needed.
- [ ] Update project documentation and test/deployment scripts to describe the new implementation.

## Validation

- [x] Add unit tests for backend lifecycle, configuration, readiness, and failure handling.
- [ ] Add integration tests for the backend interface and playback/control state synchronization.
- [ ] Test startup, playback, pause/resume, stop/idle, shutdown, and unexpected backend exit for the new implementation.
- [ ] Test audio-device selection and release/reacquire while Kodi and Sendspin contend for audio.
- [ ] Test two-way volume/mute, playback timing, metadata, and Kodi player/UI state.
- [ ] Test clear failure behavior when the backend is missing, incompatible, or fails to start.
- [ ] Run `ruff check --fix .` and `ruff format .`.
- [ ] Run `pre-commit run --all-files`.
- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Update and run applicable Kodi smoke/API tests for the new implementation; do not require Docker for these tests.
- [ ] Perform and record physical-device testing for LibreELEC and representative Debian/Fedora systems.
- [x] Record tested OS versions, CPU architectures, dependency versions, results, and known limitations.

## Completion Criteria

- [ ] Docker-specific code and configuration have been removed from the add-on and its active test/deployment paths.
- [ ] The new backend builds and runs on every agreed target.
- [ ] Required playback, audio handoff, synchronization, volume, and Kodi UI behavior passes validation.
- [ ] The integration uses the external `sendspin-cpp` dependency without modifying or duplicating its library implementation.
- [ ] Automated checks and platform-specific validation pass, with exceptions documented.
- [ ] Documentation describes the new dependency, build/run requirements, and known limitations.
