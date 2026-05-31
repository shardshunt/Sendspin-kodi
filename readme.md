# Sendspin Audio Plugin for Kodi

`plugin.audio.sendspin` is a Kodi music provider add-on that integrates Kodi with a Sendspin playback backend running in Docker.

The add-on launches a local `sendspin-cli` daemon container, exposes a local control API, and synchronises Sendspin playback state, metadata, and volume with Kodi.

## Disclaimer

This add-on is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## What it does

- Runs as a Kodi audio provider plugin (`plugin.audio.sendspin`).
- Starts a Docker container to host the Sendspin daemon.
- Uses a local HTTP control API to keep Kodi and Sendspin in sync.
- Maps Kodi playback metadata into Kodi's native player UI.
- Keeps a silent dummy Kodi track playing so ALSA device ownership is retained.
- Automatically pulls the configured Docker image if it is missing locally.

## Requirements

- `docker` must be installed and available in the host system `$PATH`. (eg with the docker addon for LibreElec)

## Installation

1. Dowload the latest plugin.audio.sendspin.zip fron releases.
2. Open Kodi.
3. Go to **Settings** → **Add-ons**.
4. Choose **Install from zip file**.
5. Select the generated `plugin.audio.sendspin.zip`.
6. Wait for the installation to complete.

## Usage

Launch the add-on from Kodi like any music provider.
On the first lauch a dialoge will show the pull status for the docker image. **Note: This can take a long time**
If the Plugin crashes you may need to correct the audio device in settings.

## Configuration

Configure the add-on from Kodi settings. Current settings include:

- `Local proxy port` – local port for the control API (default `59999`).
- `Container control API URL` – local control URL (`http://127.0.0.1:59999` by default).
- `Static playback delay (ms)` – optional timing offset for playback.
- `Docker container name` – default `sendspin-player`.
- `Docker image name` – default `ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi`.
- `Use addon version for image tag` – enables auto-pull of the version-tagged image.
- `Docker image tag override` – override the pulled image tag.
- `Docker config directory` – default `/storage/.config/sendspin`.
- `Start Docker backend` – disable container startup for API-only or test runs.
- `Audio device ID override` – force a specific ALSA device index.
- `Kodi to Sendspin volume scale` – scale factor for volume mapping.
- `Fallback audio device ID` – used when device detection cannot resolve the current output.
- `Enable multi-instance guard` – prevent multiple running instances.
- `Activate visualisation window` – optionally show the visualisation UI.
- `Stop when dummy playback stops` – whether the add-on shuts down when its dummy playback ends.

## Notes on implimentation:

The add-on supports both internal Kodi actions and the Sendspin control API.

### Local control API

The container is configured to expose a local HTTP control API. See `SENDSPIN_CONTROL_API.md` for the exact API contract.

Default control API settings:

- `http://127.0.0.1:59999`
- `POST /control` for playback commands
- `GET /state` for current track/playback/volume state

### Docker and image behavior

- The add-on uses the configured Docker image and container name.
- By default, it pulls `ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi` using the add-on version as the image tag.
- If `docker_image_tag_override` is set, that tag is used instead.
- The add-on mounts `/dev/snd` into the container and uses host networking.
- The container stores its runtime configuration under the configured Docker config directory.

### Tests

The repository includes Kodi smoke tests and API scenario coverage:

- `tests/kodi/smoke.sh` — container-based Kodi smoke test harness.
- `tests/kodi/api_scenarios.sh` — exercises the documented control API and plugin routes.
- `tests/docker_image_pull_start_test.sh` — validates Docker image pull and container start behavior.

The smoke harness uses Podman when available and can also run with Docker Compose.

### Release and packaging

A helper script is available to package and publish the add-on:

- `python scripts/release.py --check` — validate version alignment and build `plugin.audio.sendspin.zip`.
- `python scripts/release.py --publish` — create a GitHub release and upload the zip asset.

The add-on version is read from both `plugin.audio.sendspin/addon.xml` and `pyproject.toml`, and they must match.

### Documentation

See `SENDSPIN_CONTROL_API.md` for the local control API contract and example curl commands.
