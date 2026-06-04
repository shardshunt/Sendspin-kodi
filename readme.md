# Sendspin Audio Service for Kodi

`service.sendspin` is a Kodi background service add-on that integrates Kodi with a Sendspin playback backend running in Docker.

The add-on automatically launches a local [`sendspin-cli`](https://github.com/shardshunt/sendspin-cli-for-sendspin-kodi) daemon container, exposes a local control API, and synchronises Sendspin playback state, metadata, and volume with Kodi.

## Disclaimer

This add-on is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## Installation

### LibreElec:

  1. Install the docker addon for LibreELEC.
  2. Dowload the latest service.sendspin.zip fron releases.
  3. Open Kodi.
  4. Go to **Settings** → **Add-ons**.
  5. Choose **Install from zip file**.
  6. Select `service.sendspin.zip` in the dowloaded location.
  7. Upon first use the plugin will pull the docker image, this may take a while.

### Debian:
*These steps are **not** needed on LibreELEC, as Kodi runs as `root` there by default and uses ALSA natively.*

  1. Install docker
      ```bash
      sudo apt install docker
      ```

  2. The user running Kodi must have permission to execute Docker commands without `sudo`. This is done by adding the user to the `docker` group:
     ```bash
     sudo usermod -aG docker $USER
     sudo reboot
     ```
     *(Note: A reboot of the host computer or a full user logout/login is required after running this command for the group changes to take effect).*

  3. (Not always necissary) Kodi may need to be configured to run with the `KODI_AE_SINK=ALSA` environment variable to ensure direct ALSA hardware access. To set this system-wide, add the variable to `/etc/environment`:
     ```bash
     echo "KODI_AE_SINK=ALSA" | sudo tee -a /etc/environment
     ```
     *(Note: You will need to log out and back in, or reboot the machine, for this to take effect).*

  4. Follow Steps 2-7 of the LibreElec Instructions

## Usage

The service runs automatically in the background on Kodi startup. Playback is started and controlled remotely from your Sendspin server (e.g., Music Assistant), and Kodi automatically synchronises its active player state, metadata, OSD, and volume.

On the first start, a progress dialog will show the pull status of the docker image if it's not present locally. **Note: This can take a long time.**
If the addon encounters issues, verify that your audio output settings and ALSA configurations are set correctly.

## Configuration

Note that Static Delay is set from the setting menu, change and apply the value for it to update:

- `Static playback delay (ms)` – optional timing offset for playback.

Current other settings include:

- `Local proxy port` – local port for the control API (default `59999`).
- `Container control API URL` – local control URL (`http://127.0.0.1:59999` by default).
- `Docker container name` – default `sendspin-player`.
- `Docker image name` – default `ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi`.
- `Docker config directory` – default `/storage/.config/sendspin`.
- `Start Docker backend` – disable container startup for API-only or test runs.
- `Audio device ID override` – force a specific ALSA device index.
- `Kodi to Sendspin volume scale` – scale factor for volume mapping.
- `Fallback audio device ID` – used when device detection cannot resolve the current output.
- `Enable multi-instance guard` – prevent multiple running instances.
- `Activate visualisation window` – optionally show the visualisation UI.
- `Stop when dummy playback stops` – whether the add-on shuts down when its dummy playback ends.

*(Note: Docker image tag is defined in `service.sendspin/docker_image_version.txt`.)*

## What it does

- Runs as a Kodi background service (`service.sendspin`).
- Starts a Docker container to host the Sendspin daemon.
- Uses a local HTTP control API to keep Kodi and Sendspin in sync.
- Maps Kodi playback metadata into Kodi's native player UI.
- Keeps a silent dummy Kodi track playing while Sendspin is active so Kodi's player UI stays in sync.
- Uses the Sendspin control API to release the backend audio stream when playback is paused or idle so Kodi can reclaim the audio device immediately (e.g. for OSD navigation sounds or alternative local audio playback).
- Automatically pulls the configured Docker image if it is missing locally.

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

The container is configured to expose a local HTTP control API. See `SENDSPIN_CONTROL_API.md` for the exact API contract.

Default control API settings:

- `http://127.0.0.1:59999`
- `POST /control` for playback commands
- `GET /state` for current track/playback/volume state

### Docker and image behavior

- The add-on uses the configured Docker image and container name.
- It pulls `ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi` using the version in `service.sendspin/docker_image_version.txt`.
- The add-on mounts `/dev/snd` into the container and uses host networking.
- The container stores its runtime configuration under the configured Docker config directory.

### Tests

The repository includes Kodi smoke tests and API scenario coverage:

- `tests/kodi/smoke.sh` — container-based Kodi smoke test harness.
- `tests/kodi/api_scenarios.sh` — exercises the documented control API and plugin routes.
- `tests/docker_image_pull_start_test.sh` — validates Docker image pull and container start behavior.

The smoke harness uses Podman when available and can also run with Docker Compose.

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
    - **Docker Version Source**: Validates `service.sendspin/docker_image_version.txt` exists and is readable.
    - **Remote Validation**: Verifies the release tag is available and the Docker image exists on GHCR (requires `GITHUB_TOKEN`).
    - **ZIP Layout**: Builds and verifies the final ZIP structure meets Kodi standards.
- `python scripts/release.py --publish --token GITHUB_TOKEN` — Performs all checks and, if successful, creates a GitHub release and uploads the asset.
- `python scripts/release.py --publish --force --token GITHUB_TOKEN` — Bypasses non-critical validation failures (e.g., git cleanliness) to force a release.

Plugin versions must follow the `YYYY.Month.Patch` format (e.g., `2026.5.0`).

### Documentation

See `SENDSPIN_CONTROL_API.md` for the local control API contract and example curl commands.
