# Sendspin Audio Service for Kodi

`service.sendspin` is being migrated from Docker-hosted playback to a native integration using the external [`sendspin-cpp`](https://github.com/Sendspin/sendspin-cpp) project.

This branch contains an experimental native process and Kodi supervisor, but the executable is not included in the add-on ZIP, server discovery and device selection are incomplete, and real playback has not been validated. Do not package or deploy this branch for normal use yet.
## Disclaimer

This add-on is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## Installation

Installation instructions are intentionally omitted while this branch is incomplete. Supported operating systems, architectures, native dependency packaging, and minimum versions still need validation.

The native backend is staged into the package with CMake, a C++20 compiler, PortAudio/ALSA development files, and network access for the pinned `sendspin-cpp` dependency. The recommended host-clean build uses Podman:

```bash
scripts/build_native_backend_container.sh
```

This builds a Fedora 44 development image and writes only the resulting
target-specific executable into the add-on package. The direct
`scripts/build_native_backend.sh` command remains available inside a
matching development container or distrobox.

Release packaging requires the resulting executable at
`service.sendspin/resources/bin/sendspin-kodi-backend`; the release ZIP includes
that file automatically.

## Usage

Kodi can supervise the native process when its executable path is configured. The prototype currently uses the host's default PortAudio device and requires an explicit server URL or direct inbound connection; mDNS discovery and actual Sendspin playback remain unverified.

## Configuration

Note that Static Delay is set from the setting menu, change and apply the value for it to update:

- `Static playback delay (ms)` – optional timing offset for playback.

Current other settings include:

- `Local proxy port` – local port for the control API (default `59999`).
- `Local control API URL` – temporary local control URL (`http://127.0.0.1:59999` by default); the native backend will replace this interface.
- `Audio device ID override` – reserved for native output selection, which is not wired in this prototype yet.
- `Kodi to Sendspin volume scale` – scale factor for volume mapping.
- `Fallback audio device ID or ALSA name` – reserved for native output selection, which is not wired in this prototype yet.
- `Enable multi-instance guard` – prevent multiple running instances.
- `Activate visualisation window` – optionally show the visualisation UI.
- `Stop when dummy playback stops` – whether the add-on shuts down when its dummy playback ends.

## What it does

- Runs as a Kodi background service (`service.sendspin`).
- Retains Kodi-side player/UI and session synchronization scaffolding for the native backend.
- Maps Kodi playback metadata into Kodi's native player UI.
- Keeps a silent dummy Kodi track playing while Sendspin is active so Kodi's player UI stays in sync.
- Uses the Sendspin control API to release the backend audio stream when playback is paused or idle so Kodi can reclaim the audio device immediately (e.g. for OSD navigation sounds or alternative local audio playback).

## Notes on implementation:

The add-on supports both internal Kodi actions and the Sendspin control API.

### Audio Device Releasing and Reclaiming Lifecycle

To avoid hardware contention and ALSA device locking issues, the service manages a seamless state loop:
- **Pause**: When Sendspin transitions to a paused state, the addon immediately releases the hardware audio device (e.g., HDMI 4) back to Kodi by restoring Kodi's original audio output device and triggering `release_audio` on the Sendspin daemon. The dummy Kodi player is kept in a **paused** state to preserve the Kodi OSD and playlist context.
- **Stop/Idle**: When Sendspin transitions to an idle state (no active playback and no track info), the addon observes a **2.5-second grace period** (to filter out temporary pauses during stream resets). If the idle state persists, it releases the hardware audio device and **stops** the dummy Kodi player.
- **Play/Resume**: When Sendspin transitions to playing, the addon:
  1. Switches Kodi to a silent alternate candidate (e.g., `ALSA:default` or `ALSA:sysdefault`) to free the hardware device.
  2. Sleeps for 1.5 seconds to allow Kodi's audio engine (ActiveAE) to completely release the physical ALSA sound card.
  3. Calls `acquire_audio` on the Sendspin daemon.
  4. Sleeps for 0.5 seconds to let the daemon lock the hardware device.
  5. Triggers an unconditional stream reset (pause/play toggle) to force Music Assistant to send new FLAC headers, resolving silent playback caused by discarded stream headers.
  6. Updates track metadata dynamically in Kodi's UI on the fly via `updateInfoTag` to prevent playback restarts and window focus loss.


### Local control API

The Kodi-side controller still documents its temporary local HTTP contract in `SENDSPIN_CONTROL_API.md`. Phase 2 will replace it with the native backend interface.

Default control API settings:

- `http://127.0.0.1:59999`
- `POST /control` for playback commands
- `GET /state` for current track/playback/volume state

### Tests

The repository includes Kodi smoke tests and API scenario coverage:

- `tests/kodi/smoke.sh` — container-based Kodi smoke test harness.
- `tests/kodi/api_scenarios.sh` — exercises the documented control API and plugin routes.
The Kodi smoke harness uses Podman when available and can optionally use Docker Compose to run Kodi itself; neither path starts a Sendspin playback container.

### Release and packaging

Before packaging, ensure all Python dependencies listed in `pyproject.toml` are synced to the add-on's local library folder:

```bash
python scripts/get_libs.py
```

This populates `service.sendspin/resources/lib` so the libraries are available within Kodi's isolated Python environment.

A helper script is available to package and publish the add-on:

- `python scripts/release.py --check --token GITHUB_TOKEN` — Runs a comprehensive validation suite:
    - **Git Integrity**: Ensures the working tree is clean and synced with `origin/main`.
    - **Version Check**: Validates the `YYYY.Month.Patch` calendar versioning format.
    - **Metadata Alignment**: Syncs versions across `addon.xml` and `pyproject.toml`.
    - **Remote Validation**: Verifies the release tag is available (requires `GITHUB_TOKEN`).
    - **ZIP Layout**: Builds and verifies the final ZIP structure meets Kodi standards.
- `python scripts/release.py --publish --token GITHUB_TOKEN` — Performs all checks and, if successful, creates a GitHub release and uploads the asset.
- `python scripts/release.py --publish --force --token GITHUB_TOKEN` — Bypasses non-critical validation failures (e.g., git cleanliness) to force a release.

Plugin versions must follow the `YYYY.Month.Patch` format (e.g., `2026.5.0`).

### Documentation

See `SENDSPIN_CONTROL_API.md` for the local control API contract and example curl commands.
