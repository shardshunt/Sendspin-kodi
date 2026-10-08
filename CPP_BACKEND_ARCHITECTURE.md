# Sendspin C++ Backend Architecture

Status: Draft; Phase 1 complete, Phase 2 prototype in progress
Last updated: 2026-10-09

## Summary

Replace the Docker-hosted playback backend with a small host-native process linked against the external `sendspin-cpp` library. The official `sendspin-cpp-cli` was evaluated and rejected as a direct backend because its control/state surface cannot preserve Kodi's current behavior.

Phase 1 removed Docker process management and image configuration. Phase 2 now has a native C++ process prototype linked to the external `sendspin-cpp` library, with Kodi process supervision and a private control socket. It is not yet a releasable playback backend: discovery, selected-device routing, hardware streaming, and packaging remain open.

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

Kodi volume and backend player volume are both integers from 0 to 100; the existing scale defaults to `0.3`. Mute is a boolean. Server-originated player volume/mute changes update the sink and Kodi, while Kodi-originated changes update the local player role and sink; group `controller@v1` volume remains separate. Kodi delay is milliseconds, clamped to 0-5000. The sink reports consumed frames and their monotonic finish timestamp, while `sendspin-cpp` owns clock synchronization. The current PortAudio sink uses the host default; Kodi's output override/fallback is not wired yet.

### Lifecycle and Failure Handling

The Python service launches a configured native binary, passes its name/server/state/socket paths, seeds volume/mute/delay after readiness, forwards logs, and stops only its child. Readiness is a successful state response within 15 seconds. Child exit or socket loss is detected with a five-second restart backoff; wake and audio-setting changes request a restart. A missing/incompatible binary is logged and leaves Kodi's audio route unchanged. The executable is not bundled in the add-on yet.

The socket can release/reacquire the sink while the Sendspin client remains active. That behavior has only been exercised while idle; real pause/resume contention and shutdown during a stream remain unverified.

### Configuration, Security, and Compatibility

Pass player name, optional outbound URL, profile-owned state file, and socket path to the backend. The prototype does not advertise over mDNS, so it needs a server URL or direct inbound connection. State and pairing data are owner-only. The CMake project pins `sendspin-cpp` v0.9.3 at commit `91038b72892596b9ec0cccfec1b9e6ebc58ba148`; target packaging and ABI support remain unverified.

## Alternatives Considered

| Alternative | Benefits | Drawbacks | Decision |
| --- | --- | --- | --- |
| Official `sendspin-cpp-cli` | Existing host player, audio sinks, state, controls, persistence, release binaries | No audio release/reacquire command; status is human-readable and omits Kodi metadata | Rejected as direct backend |
| Small host integration against `sendspin-cpp` | Direct public API; can expose required state and own audio handoff | Requires maintaining a host process and audio sink | Selected for prototype |
| Continue Docker or retain it as fallback | Known current behavior | Explicitly out of scope for this branch | Rejected |

## Testing and Operations

Completed CLI probe on Bazzite 44 x86_64: verified the v0.3.0 archive SHA256, inspected its build metadata and glibc/runtime requirements, listed host audio outputs, and exercised `status`, `pause`, and unsupported `release_audio` against a null-output instance. The CLI does not satisfy Kodi's audio handoff or state contract.
Phase 1 removed Docker process management, settings, image metadata, GHCR validation, deployment-time container removal, and Docker-only tests. The ignored `service.sendspin.zip` was not regenerated: it is a stale Docker package, and this incomplete branch is explicitly not releasable.

The Podman Kodi smoke harness booted the add-on and exercised the mock HTTP API without a playback container. Its log also showed `asyncio.run()` being called from an already-running loop during repeated service entry. The API scenario harness failed its plugin-command assertion: Kodi logged `GetDirectory` errors for the plugin action URLs, and `addon.xml` currently declares only the service extension. These are open Kodi lifecycle/route issues; neither was changed as part of Docker removal.

Completed CLI probe on Bazzite 44 x86_64: verified the v0.3.0 archive SHA256, inspected its build metadata and glibc/runtime requirements, listed host audio outputs, and exercised `status`, `pause`, and unsupported `release_audio` against a null-output instance. The CLI does not satisfy Kodi's audio handoff or state contract.

The project-owned `native/sendspin-kodi-backend` target builds on Bazzite 44 x86_64 with `sendspin-cpp` v0.9.3 at commit `91038b72892596b9ec0cccfec1b9e6ebc58ba148`. PortAudio and ALSA development RPM contents were staged under `/tmp`; no host packages were installed. The pinned upstream PortAudio sink and file-persistence example helpers are compiled directly from `examples/common`.

The backend passes version/help probes and a local process integration probe: Kodi launched it, observed socket readiness, verified initial scaled volume/mute and delay seeding, queried the Kodi-shaped state, changed volume/delay, released/reacquired audio, and stopped the child. No server was connected and no audio stream reached the DAC, so synchronization, real playback, and hardware handoff remain unverified.

The Kodi API scenario harness passes after registering the add-on's plugin entry point. The Podman smoke harness boots and exercises the mock HTTP path; it reports that the native executable is not packaged, as expected. Physical Kodi lifecycle and real-server playback tests remain outstanding.

Next, add mDNS discovery and selected-device support, package the native executable, and test actual Sendspin audio plus sink release/reopen on a target system.

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
