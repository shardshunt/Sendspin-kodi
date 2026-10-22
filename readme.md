# Sendspin Audio Service for Kodi

`service.sendspin` integrates Kodi with the [Sendspin](https://github.com/Sendspin) ecosystem using a native C++ backend built upon [`sendspin-cpp`](https://github.com/Sendspin/sendspin-cpp). It allows Kodi devices (including LibreELEC) to act as synchronized audio playback endpoints with automatic ALSA audio device handoff.

## Disclaimer

This add-on is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## Building Native Backends

The native backend (`sendspin-kodi-backend`) is compiled with CMake, C++20, and ALSA/PortAudio support, with pinned `sendspin-cpp` dependencies. Multi-architecture builds are supported for `x86_64`, `aarch64`, and `armv7`.

The recommended build uses a containerized Ubuntu 22.04 LTS cross-compilation environment (Podman):

```bash
# Build the complete multi-arch matrix (x86_64, aarch64, armv7):
scripts/build_native_backends_all.sh

# Or build a specific target:
NATIVE_ARCH=aarch64 scripts/build_native_backend_ubuntu_container.sh
```

Compiled executables are staged per architecture under:
- `service.sendspin/resources/bin/x86_64/sendspin-kodi-backend`
- `service.sendspin/resources/bin/aarch64/sendspin-kodi-backend`
- `service.sendspin/resources/bin/armv7/sendspin-kodi-backend`

The direct `scripts/build_native_backend.sh` command can also be run directly on the host or inside a development container.

## Deployment & Testing on Kodi / LibreELEC

For development deployment to a remote Kodi or LibreELEC box:

```bash
# Upload and restart service on default IP (192.168.0.177):
./upload

# Or specify custom IP:
KODI_IP=192.168.0.x ./upload
```

The script streams the add-on files while preserving remote Python libraries in `resources/lib/`.

## Usage

When the service starts, it automatically detects the host architecture (`platform.machine()`) and launches the corresponding backend binary from `resources/bin/<arch>/sendspin-kodi-backend` (falling back to legacy unnested path if needed).

The backend connects to the Sendspin server (e.g., Music Assistant) and advertises Kodi as a synchronized player. Communication between the Kodi Python service and the native backend occurs over a high-performance UNIX domain socket (`control.sock`).

## Configuration

Settings are configurable via the add-on settings menu in Kodi:

### Playback
- `Static playback delay (ms)` – optional timing offset for synchronized playback (e.g., to compensate for AVR audio processing latency).
- `Kodi to Sendspin volume scale` – scale factor for volume mapping (default: `0.3`).

### Connection
- `Sendspin server WebSocket URL (optional)` – explicit server URL (e.g. `ws://192.168.0.x:8927/sendspin`); leave empty for discovery.
- `Local proxy port` – local port for the HTTP control API (default `59999`).
- `Local control API URL` – local control API endpoint (`http://127.0.0.1:59999` by default).

### Client
- `Native backend executable path` – optional manual override path to `sendspin-kodi-backend` (leave empty to auto-detect architecture binary).
- `Sendspin player name` – player name announced to Sendspin (default: `Kodi`).
- `Audio device override` – ALSA device identifier override (e.g. `hw:2,0`; leave empty to auto-detect).
- `Fallback audio device ID or ALSA name` – fallback device ID or ALSA name.
- `Enable multi-instance guard` – prevent multiple conflicting instances from running.
- `Activate visualisation window` – automatically display Kodi's visualization / music player screen during playback.
- `Stop when dummy playback stops` – whether the service stops Sendspin playback when Kodi's player stops.

## What it does

- Runs as a Kodi background service (`service.sendspin`).
- Automatically supervises and recovers the native `sendspin-kodi-backend` process.
- Coordinates hardware audio handoff between Kodi and the native ALSA player without conflicts.
- Maps playback metadata and album art dynamically into Kodi's native player UI.
- Keeps a silent dummy Kodi track playing during active Sendspin playback to maintain Kodi screensaver and OSD status.

## Notes on implementation:

### Audio Device Releasing and Reclaiming Lifecycle

To avoid hardware contention and ALSA device locking issues, the service manages a seamless state loop:
- **Pause**: When Sendspin transitions to a paused state, the addon immediately releases the hardware audio device back to Kodi by restoring Kodi's original audio output device and triggering `release_audio` on the Sendspin daemon. The dummy Kodi player is kept in a **paused** state to preserve the Kodi OSD and playlist context.
- **Stop/Idle**: When Sendspin transitions to an idle state (no active playback and no track info), the addon observes a **2.5-second grace period** (to filter out temporary pauses during stream resets). If the idle state persists, it releases the hardware audio device and **stops** the dummy Kodi player.
- **Play/Resume**: When Sendspin transitions to playing, the addon:
  1. Switches Kodi to a silent alternate candidate (e.g., `ALSA:default` or `ALSA:sysdefault`) to free the hardware device.
  2. Sleeps for 1.5 seconds to allow Kodi's audio engine (ActiveAE) to completely release the physical ALSA sound card.
  3. Calls `acquire_audio` on the Sendspin daemon.
  4. Sleeps for 0.5 seconds to let the daemon lock the hardware device.
  5. Triggers an unconditional stream reset (pause/play toggle) to force Music Assistant to send new FLAC headers, resolving silent playback caused by discarded stream headers.
  6. Updates track metadata dynamically in Kodi's UI on the fly via `updateInfoTag` to prevent playback restarts and window focus loss.

### Local Control API

The service communicates with the native backend through a local UNIX domain socket (`control.sock`) located in Kodi's addon userdata directory. For testing and external integrations, an HTTP proxy interface is also provided:

- `http://127.0.0.1:59999`
- `POST /control` for playback commands
- `GET /state` for current track/playback/volume state

See `SENDSPIN_CONTROL_API.md` for the full HTTP contract and curl examples.

### Tests

The test suite covers unit testing, container smoke tests, and native hardware playback:

- **Unit Tests**:
  ```bash
  python3 -m unittest discover tests
  ```
  Covers audio release/acquire lifecycle state transitions, volume scaling, socket client fallbacks, and architecture binary resolution.

- **Kodi Container Smoke Harness**:
  ```bash
  ./tests/kodi/smoke.sh
  ./tests/kodi/api_scenarios.sh
  ```
  Runs headless Kodi in a container to verify add-on loading and API scenario coverage.

- **Native Playback Test**:
  ```bash
  ./tests/kodi/native_playback_test.sh
  ```
  Full end-to-end integration test with live Music Assistant server and ALSA hardware output verification.

### Release and packaging

Before packaging, sync all Python dependencies listed in `pyproject.toml` into the add-on:

```bash
python scripts/get_libs.py
```

Build the multi-architecture native backends:

```bash
scripts/build_native_backends_all.sh
```

Package and validate the add-on using `release.py`:

```bash
# Validate git cleanliness, versioning, and presence of all native backends (x86_64, aarch64, armv7):
python scripts/release.py --check --token GITHUB_TOKEN

# Publish a GitHub release:
python scripts/release.py --publish --token GITHUB_TOKEN

# Force-publish (bypassing non-critical git check failures):
python scripts/release.py --publish --force --token GITHUB_TOKEN
```

Plugin versions must follow the `YYYY.Month.Patch` calendar format (e.g., `2026.5.0`).
