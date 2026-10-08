# Sendspin C++ Backend Architecture

Status: Draft; Phase 1 strip complete, native backend not integrated
Last updated: 2026-10-09

## Summary

Replace the Docker-hosted playback backend with a small host-native process linked against the external `sendspin-cpp` library. The official `sendspin-cpp-cli` was evaluated and rejected as a direct backend because its control/state surface cannot preserve Kodi's current behavior.

Do not remove Docker process management until audio handoff, state/control access, and a target-system build have been demonstrated. The official CLI was tested and is not a drop-in replacement; the leading candidate is a small host process linked against the external `sendspin-cpp` library.
Docker process management and its container/image settings have been removed in Phase 1. The official CLI was tested and is not a drop-in replacement; the leading candidate is a small host process linked against the external `sendspin-cpp` library. Phase 1 intentionally leaves playback unavailable until that native process is implemented; the existing HTTP control client is transitional test scaffolding, not a Docker fallback.

## Context

The Kodi service currently launches `DockerPlaybackEngine` and talks to the container over the HTTP API documented in [SENDSPIN_CONTROL_API.md](SENDSPIN_CONTROL_API.md). Kodi relies on that API for track/playback/volume state, transport and volume commands, startup readiness, recovery, and explicit `release_audio` / `acquire_audio` during audio-device handoff. The service also uses `audio_status` to detect handoff and backend recovery conditions.

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

The CLI is rejected as the direct Kodi backend: it lacks audio release/reacquire and a complete machine-readable state interface. Prototype a small C++ host process using the documented public `sendspin-cpp` API. It owns the Sendspin client and roles, writes decoded audio to a selected host sink with timing feedback, and exposes local machine-readable state/control to the Kodi Python service. Kodi remains the process supervisor and UI adapter; protocol, decoding, and synchronization remain in the external library. The prototype is not production-ready until real output and audio-device handoff pass on a target.

### Components and Responsibilities

| Component | Responsibility | Changes |
| --- | --- | --- |
| Kodi service | Kodi UI/player synchronization, configuration, backend supervision, audio handoff policy | Replace Docker supervisor and HTTP transport after prototype proves parity |
| `sendspin-cpp` | Sendspin protocol, decoding, synchronized playback roles | External pinned dependency; no local library changes |
| Host backend process | Sendspin client/roles, audio sink and timing feedback, persistent identity, local state/control, readiness and logs | New C++ executable linked to the pinned public API; JSON-over-UDS contract below |

### Interfaces and Data Flow

Current flow: Kodi Python service -> Docker process -> HTTP control API -> Sendspin daemon. Candidate flow: Kodi Python service -> supervised C++ host process -> `sendspin-cpp` roles and audio sink.
Previous flow: Kodi Python service -> Docker process -> HTTP control API -> Sendspin daemon. Phase 1 removes the process/image owner and keeps the HTTP controller/session code as transitional scaffolding. Candidate flow: Kodi Python service -> supervised C++ host process -> `sendspin-cpp` roles and audio sink.

Use a private Unix-domain socket with one JSON request and response per connection. Preserve the current state object and command names where possible. Required operations are state query; play/pause/next/previous/seek; local volume/mute update; output-delay update; and explicit audio release/acquire/status. Include a protocol version and structured errors. The C++ main loop owns both `SendspinClient::loop()` and socket dispatch so main-loop-only role APIs are not called from a socket thread. Python polls state and sends commands with bounded timeouts.

The player role supplies decoded PCM through `on_audio_write()`. The sink reports frames-played timing through `PlayerRole::notify_audio_played()`. Player, metadata, artwork, and controller callbacks update the backend state snapshot; Kodi continues to own the dummy player, metadata tags, artwork presentation, and UI.

Kodi volume and backend player volume are both integers from 0 to 100; the existing configurable scale defaults to `0.3` and is applied in both directions. Mute is a boolean. Server-originated player volume/mute changes update the sink and Kodi, while Kodi-originated changes update the local player role and sink; group `controller@v1` volume remains a separate server command. Kodi's delay setting is milliseconds, clamped to 0-5000; the host adapter maps it to the library's output-delay API. The sink reports consumed frames and their monotonic finish timestamp, while `sendspin-cpp` owns protocol clock synchronization and chunk scheduling. Device selection starts from Kodi's current audio-device value, with the add-on override and fallback settings taking precedence as they do today.

### Lifecycle and Failure Handling

The Python service starts the backend, waits for an explicit ready response, then polls state and supervises process health. A ready process with no server connection is not a startup failure; connection state is reported separately. On Kodi wake, restart or reconnect the client without losing its persisted identity. On unexpected exit or an unresponsive local socket, report a clear error and use bounded restart attempts without blocking Kodi's event loop. A build failure fails packaging/CI and must not publish a partial add-on. A missing or incompatible runtime binary shows a Kodi-visible startup error, leaves Kodi's audio route untouched, and does not fall back to Docker.

The backend opens the selected sink when playback becomes active. A pause/idle release closes the sink but leaves the Sendspin client, protocol connection, and metadata alive. Resume waits for Kodi's physical output to be released, then reopens the sink and reports audio active. Shutdown stops the client, closes the sink and socket, and exits cleanly. Missing binary/library, incompatible runtime, failed device open, and unexpected process exit must be visible in Kodi logs and must not leave Kodi switched away from its original device.

### Configuration, Security, and Compatibility

Pass the selected device, friendly player name, profile-owned state directory, delay, and socket path to the backend. Store pairing identity and volume/delay persistence in the add-on profile with owner-only permissions. The socket is owner-only and rejects malformed or oversized requests. Pin `sendspin-cpp` v0.9.3 (source commit `91038b72892596b9ec0cccfec1b9e6ebc58ba148`) for the prototype; this is not a final production pin until target builds and packaging are verified. LibreELEC, Debian, and Fedora minimum versions and CPU architectures remain unknown.
- The CLI v0.3.0 release was the latest release observed on 2026-10-09 and is rejected as the Kodi backend. The direct-library prototype is pinned to a release commit; do not build from moving `main` in production.

## Alternatives Considered

| Alternative | Benefits | Drawbacks | Decision |
| --- | --- | --- | --- |
| Official `sendspin-cpp-cli` | Existing host player, audio sinks, state, controls, persistence, release binaries | No audio release/reacquire command; status is human-readable and omits Kodi metadata | Rejected as direct backend |
| Small host integration against `sendspin-cpp` | Direct public API; can expose required state and own audio handoff | Requires maintaining a host process and audio sink | Selected for prototype |
| Continue Docker or retain it as fallback | Known current behavior | Explicitly out of scope for this branch | Rejected |

## Testing and Operations

Completed CLI probe on Bazzite 44 x86_64: verified the v0.3.0 archive SHA256, inspected its build metadata and glibc/runtime requirements, listed host audio outputs, and exercised `status`, `pause`, and unsupported `release_audio` against a null-output instance. The CLI does not satisfy Kodi's audio handoff or state contract.
Phase 1 removed Docker process management, settings, image metadata, GHCR validation, deployment-time container removal, and Docker-only tests. The ignored `service.sendspin.zip` was not regenerated: it is a stale Docker package, and this incomplete branch is explicitly not releasable.
Phase 1 removed Docker process management, settings, image metadata, GHCR validation, deployment-time container removal, and Docker-only tests. The ignored `service.sendspin.zip` was not regenerated: it is a stale Docker package, and this incomplete branch is explicitly not releasable.

The Podman Kodi smoke harness booted the add-on and exercised the mock HTTP API without a playback container. Its log also showed `asyncio.run()` being called from an already-running loop during repeated service entry. The API scenario harness failed its plugin-command assertion: Kodi logged `GetDirectory` errors for the plugin action URLs, and `addon.xml` currently declares only the service extension. These are open Kodi lifecycle/route issues; neither was changed as part of Docker removal.

Completed CLI probe on Bazzite 44 x86_64: verified the v0.3.0 archive SHA256, inspected its build metadata and glibc/runtime requirements, listed host audio outputs, and exercised `status`, `pause`, and unsupported `release_audio` against a null-output instance. The CLI does not satisfy Kodi's audio handoff or state contract.

The pinned `sendspin-cpp` v0.9.3 library and upstream `basic_client` consumer compiled and linked on this host. The build used `BUILD_EXAMPLES=ON`, tests off, source role off, and Opus off. PortAudio and mDNS were not enabled because their development headers are absent, so the example used its null/discard audio path. This verifies CMake integration and public role API linkage, but not a real audio sink or synchronized playback.

Next, build a project-owned host integration against the pinned library. Its first prototype must prove a real selected audio output, timing feedback from the output device, metadata/controller callbacks, and sink release/reopen while the Sendspin client remains connected. The IPC contract above is provisional; validate it with Kodi service tests before removing any existing backend.

## Risks and Open Questions

- Can a project-owned host process link against the pinned release together with its chosen audio sink and IPC dependencies?
- How should `service_entry.py` avoid repeated `asyncio.run()` when Kodi starts/reloads the service, and should plugin actions be registered as a Kodi plugin extension or removed from the test contract?
- Which host sink can reliably close and reopen on Kodi's pause/idle and resume transitions while retaining the Sendspin client and timing state?
- What IPC protocol can represent track metadata/artwork, playback, local player volume/mute, audio handoff, readiness, and errors without blocking Kodi's event loop?
- How will stable client identity and pairing data be persisted by the host integration?
- What target OS versions, CPU architectures, minimum libc versions, and audio backends are in scope?
- Which exact `sendspin-cpp` release/commit and dependency checksums will be pinned, and how will target-specific binaries be delivered with the Kodi add-on?

## Decisions

| Date | Decision | Rationale |
| --- | --- | --- |
| 2026-10-09 | Begin with a CLI feasibility spike; do not strip Docker yet. | The CLI appears to cover playback and local controls, but its documented interface does not cover the current audio release/reacquire contract or provide the current machine-readable state shape. |
| 2026-10-09 | Reject `sendspin-cpp-cli` v0.3.0 as the direct backend; prototype against `sendspin-cpp` directly. | Runtime probing confirmed `release_audio` is unsupported and `status` is plain text missing album/artwork. Its Linux binary also needs glibc 2.38 and an Avahi compatibility library. |
| 2026-10-09 | Complete Phase 1 without generating a replacement ZIP. | The stale ZIP is ignored build output and this branch has no native playback backend yet; packaging now would produce an explicitly nonfunctional add-on. |
