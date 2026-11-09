# Sendspin C++ Backend Architecture

Status: Implemented; Phases 1 and 2 complete, multi-arch packaging and target validation active
Last updated: 2026-11-09

## Summary

Replace the Docker-hosted playback backend with a small host-native process linked against the external `sendspin-cpp` library. The official `sendspin-cpp-cli` was evaluated and rejected as a direct backend because its control/state surface cannot preserve Kodi's current behavior.

Phase 1 removed Docker process management and image configuration. Phase 2 delivered a native C++ backend process linked to the external `sendspin-cpp` library (v0.9.5), supervised by the Kodi Python service over a private Unix control socket (`control.sock`). It provides Avahi DNS-SD service advertisement (`_sendspin._tcp` on port 8928), dynamic ALSA hardware device selection (`SENDSPIN_AUDIO_DEVICE`), PortAudio DAC timing feedback for multi-room synchronization, and containerized cross-compilation for `x86_64`, `aarch64`, and `armv7`. Hardware streaming, audio handoff, and track controls have been verified on physical LibreELEC hardware.

## Context

Before Phase 1, the Kodi service launched `DockerPlaybackEngine` and used the HTTP API in [SENDSPIN_CONTROL_API.md](SENDSPIN_CONTROL_API.md). The native prototype now uses a private Unix-domain socket for state and controls; the HTTP client remains only for the Kodi mock test harness when no native executable is installed.

The official [`sendspin-cpp`](https://github.com/Sendspin/sendspin-cpp) project is a C++ protocol library. Its public integration guide describes a player role with synchronized audio output callbacks and feedback, plus controller, metadata, and artwork roles. Host builds use CMake and fetch dependencies through `FetchContent`.

The official [`sendspin-cpp-cli`](https://github.com/Sendspin/sendspin-cpp-cli) is a separate headless player built on that library. Its v0.3.0 x86_64 Linux release was checksum-verified and run on Bazzite 44 (Fedora-derived, x86_64, glibc 2.43) after staging Fedora's `libdns_sd.so.1` compatibility library under `/tmp`. It reports that it is linked against `sendspin-cpp` v0.8.0, enumerates ALSA/PulseAudio/PipeWire outputs, and starts with a null output. Its local Unix-socket interface was exercised: `status` is human-readable text, `pause` reports no server connection, and `release_audio` is rejected as an unknown command. The status omits album/artwork and does not match the current JSON state contract. The binary requires glibc 2.38 symbols and the `libdns_sd.so.1` runtime library; this is a concrete compatibility concern for older target images, not proof of compatibility on LibreELEC, Debian, or all Fedora releases.

## Minimum Kodi Behavior to Preserve

- Keep the Sendspin client connected independently of whether its local audio sink is open.
- Expose track title, artist, album, artwork URL, playback position/duration/speed, server connection, stream-active/released state, and player volume/mute to the Python service.
- Support play, pause, next, previous, seek, local player volume/mute updates, and output-delay updates. Keep group transport volume distinct from the local player's output level.
- Preserve Kodi's silent dummy-player metadata/UI synchronization while Sendspin playback is active.
- On Sendspin pause, retain the 2.5-second grace period before releasing Sendspin output and restoring Kodi's audio route. On resume, free Kodi's ALSA device before reopening Sendspin output. Preserve the stream-reset behavior if target testing confirms a fresh codec header is still required.
- Stop the dummy player when Sendspin is idle, detect backend/process/API failure, recover after Kodi wake, and restore Kodi's original audio settings on failure and shutdown.
- Retain only settings still needed: output-device override/fallback, volume mapping, playback delay, and dummy-player policy. Reassess multi-instance guard and visualisation settings during implementation.

Keep the current nested state shape from [SENDSPIN_CONTROL_API.md](SENDSPIN_CONTROL_API.md) initially: `track`, `playback`, `volume`, `connection`, and `audio`.

## Goals

- Remove the Docker runtime dependency from the add-on and its active test/deployment paths.
- Preserve required Kodi metadata, transport, volume/mute, timing, and audio handoff behavior where the chosen backend supports it.
- Keep Sendspin protocol and decoding behavior in the external C++ dependency.
- Make missing, incompatible, or failed backend startup visible and actionable.

## Non-Goals

- Modify or reimplement `sendspin-cpp` library functionality.
- Preserve Docker as a fallback or provide migration for released Docker installations.
- Claim distribution or architecture support before builds and runtime behavior are verified.

## Constraints and Assumptions

- Kodi continues to own its service lifecycle, UI, and Kodi-side player state.
- A native audio sink must report actual playback timing to the library for synchronization; accepting decoded audio without sink timing feedback is not sufficient.
- The backend must have stable identity/pairing persistence across restarts.
- Current supported behavior is defined by the service and tests, not solely by the old HTTP endpoint names; map each required behavior before implementation.
- The CLI v0.3.0 release was the latest release observed on 2026-10-09. Pin a release or commit before depending on it; do not build from moving `main` in production.

## Proposed Design

The CLI is rejected as the direct Kodi backend: it lacks audio release/reacquire and a complete machine-readable state interface. The `native/` prototype uses the public `sendspin-cpp` player, controller, and metadata roles, reports consumed-frame timing through the pinned upstream PortAudio example sink, and exposes state/control over a private Unix socket. Kodi supervises the process and owns the UI; protocol, decoding, and synchronization remain in the external library. The sink and file-persistence helpers are compiled from the pinned repository's `examples/common` directory as prototype scaffolding, not a stable public API.

### Components and Responsibilities

| Component | Responsibility | Changes |
| --- | --- | --- |
| Kodi service | Kodi UI/player synchronization, configuration, backend supervision, audio handoff policy | Launch native executable and use UDS when present; retain HTTP for the mock harness |
| `sendspin-cpp` | Sendspin protocol, decoding, synchronized playback roles | External pinned dependency; no local library changes |
| Host backend process | Sendspin client/roles, audio sink and timing feedback, persistent identity, local state/control, readiness and logs | New C++ executable linked to the pinned public API; JSON-over-UDS contract below |

### Interfaces and Data Flow

Current prototype flow: Kodi Python service -> supervised C++ process -> `sendspin-cpp` roles and PortAudio sink. The process listens on a private Unix-domain socket; the Python client uses that socket when the executable exists and retains HTTP only for the Kodi mock harness.

The implemented socket accepts one JSON request and returns one JSON response per connection. It supports state query; play/pause/toggle/next/previous/seek; local volume/mute; output delay; audio status/release/acquire; and pairing-token retrieval. State preserves `track`, `playback`, `volume`, `connection`, and `audio`. A protocol version field is still needed. The C++ main loop owns both `SendspinClient::loop()` and socket dispatch; Python uses bounded timeouts.

The player role supplies decoded PCM through `on_audio_write()`. The sink reports frames-played timing through `PlayerRole::notify_audio_played()`. Player, metadata, artwork, and controller callbacks update the backend state snapshot; Kodi continues to own the dummy player, metadata tags, artwork presentation, and UI.

Kodi volume and backend player volume are both integers from 0 to 100; the existing scale defaults to `0.3`. Mute is a boolean. Server-originated player volume/mute changes update the sink and Kodi, while Kodi-originated changes update the local player role and sink; group `controller@v1` volume remains separate. Kodi delay is milliseconds, clamped to 0-5000. The sink reports consumed frames and their monotonic finish timestamp, while `sendspin-cpp` owns clock synchronization. The PortAudio sink selects the requested ALSA device via the `SENDSPIN_AUDIO_DEVICE` environment variable (mapped by Kodi from its active audio device), falling back to the PortAudio default and first available output device.

### Lifecycle and Failure Handling

The Python service launches the native binary matching the host architecture (`resources/bin/<arch>/sendspin-kodi-backend`), passes its name/server/state/socket paths, seeds volume/mute/delay after readiness, forwards logs, and stops only its child. Readiness is a successful state response within 15 seconds. Child exit or socket loss is detected with a five-second restart backoff; wake and audio-setting changes request a restart. A missing/incompatible binary is logged and leaves Kodi's audio route unchanged.

The socket releases and reacquires the audio sink while the Sendspin client remains active. On stream start/resume, Kodi yields the hardware device to Sendspin; on pause/idle, the device is restored to Kodi. This handoff cycle has been physically verified on LibreELEC under active playback.

### Configuration, Security, and Compatibility

Pass player name, optional outbound URL, profile-owned state file, and socket path to the backend. The backend advertises over mDNS via Avahi DNS-SD (`_sendspin._tcp` on port 8928, path `/sendspin`) for discovery by Music Assistant, and can also connect directly to a configured server URL. State and pairing data are owner-only. The CMake project pins `sendspin-cpp` v0.9.5 at tag `v0.9.5` (commit `b95cf854a784ed308b1f8fe9697f414b02768ddd`). Multi-architecture binaries are cross-compiled in an Ubuntu 22.04 LTS container environment targeting `x86_64`, `aarch64`, and `armv7`.

## Alternatives Considered

| Alternative | Benefits | Drawbacks | Decision |
| --- | --- | --- | --- |
| Official `sendspin-cpp-cli` | Existing host player, audio sinks, state, controls, persistence, release binaries | No audio release/reacquire command; status is human-readable and omits Kodi metadata | Rejected as direct backend |
| Small host integration against `sendspin-cpp` | Direct public API; can expose required state and own audio handoff | Requires maintaining a host process and audio sink | Selected for prototype |
| Continue Docker or retain it as fallback | Known current behavior | Explicitly out of scope for this branch | Rejected |

## Testing and Operations

## Testing and Operations

- **Initial Feasibility Spike**: Evaluated `sendspin-cpp-cli` v0.3.0 on Bazzite 44 x86_64; confirmed that it lacks required dynamic audio release/acquire controls, omits rich metadata, and requires newer glibc and external Avahi runtime dependencies.
- **Phase 1 (Docker Stripping)**: Removed Docker process management, image configurations, GHCR workflows, and Docker-specific tests.
- **Phase 2 (Native Backend & Packaging)**: Built the project-owned `native/sendspin-kodi-backend` target linking against `sendspin-cpp` v0.9.5 (`b95cf854a784ed308b1f8fe9697f414b02768ddd`). Integrated Avahi DNS-SD mDNS advertisement (`_sendspin._tcp` on port 8928), PortAudio ALSA output device selection via `SENDSPIN_AUDIO_DEVICE`, and timing feedback via `notify_audio_played()`.
- **Cross-Compilation Matrix**: Containerized build scripts (`scripts/build_native_backends_all.sh` using `Containerfile.native-build-ubuntu`) stage multi-architecture binaries under `service.sendspin/resources/bin/<arch>/` (`x86_64`, `aarch64`, `armv7`). The release script (`scripts/release.py`) strictly enforces a fresh full matrix build prior to publishing.
- **LibreELEC Target Verification**: Tested on LibreELEC Generic x86_64 12.2.1 with Kodi 21.3 (Omega). Verified Avahi mDNS discovery by Music Assistant, dynamic ALSA device binding (`hw:1,10`), audio release/acquire lifecycle during pause/resume/stop, KORE pause forwarding, and live track metadata updates.
- **Test Automation**:
  - Unit test suite: 23 unit tests pass (`python3 -m unittest discover -s tests`).
  - Container smoke harness (`tests/kodi/smoke.sh`, `tests/kodi/api_scenarios.sh`) for headless Kodi lifecycle verification.
  - End-to-end integration harness (`tests/kodi/native_playback_test.sh`) with live Music Assistant playback.

## Risks and Open Questions

- **Physical Non-x86 Architecture Testing**: While the containerized toolchain successfully builds `aarch64` and `armv7` binaries, physical hardware validation has primarily targeted LibreELEC x86_64. Physical testing on ARM boards (e.g., Raspberry Pi) is ongoing.
- **Acoustic Latency Calibration**: While PortAudio DAC timing feedback closes the loop with `sendspin-cpp`'s drift engine, external HDMI/AVR processing delays require user tuning via `Static playback delay (ms)`. Formal multi-room acoustic latency measurements on physical hardware remain open.
- **FLAC vs PCM on Embedded**: As noted in `backend_main.cpp`, PCM is prioritized over FLAC for LibreELEC to bypass micro-FLAC decoder quirks on certain streams.

## Decisions

| Date | Decision | Rationale |
| --- | --- | --- |
| 2026-10-09 | Begin with a CLI feasibility spike; do not strip Docker yet. | The CLI appears to cover playback and local controls, but its documented interface does not cover the current audio release/reacquire contract or provide the current machine-readable state shape. |
| 2026-10-09 | Reject `sendspin-cpp-cli` v0.3.0 as the direct backend; prototype against `sendspin-cpp` directly. | Runtime probing confirmed `release_audio` is unsupported and `status` is plain text missing album/artwork. Its Linux binary also needs glibc 2.38 and an Avahi compatibility library. |
| 2026-10-09 | Complete Phase 1 without generating a replacement ZIP. | The stale ZIP is ignored build output and this branch has no native playback backend yet; packaging now would produce an explicitly nonfunctional add-on. |
| 2026-11-01 | Establish Ubuntu 22.04 LTS containerized cross-compilation matrix. | Guarantees older glibc compatibility (`GLIBC_2.34`) across `x86_64`, `aarch64`, and `armv7`, staged under `resources/bin/<arch>/`. |
| 2026-11-08 | Map Kodi HDMI audio devices to raw ALSA devices (`hw:CARD,DEV`). | Direct ALSA hardware binding bypasses software mixing buffers (dmix/Pulse/PipeWire), enabling accurate DAC delay reporting. |
| 2026-11-09 | Add Avahi DNS-SD mDNS advertisement (`_sendspin._tcp` on port 8928). | Enables seamless automatic player discovery by Music Assistant on local networks without requiring manual WebSocket URL configuration. |
