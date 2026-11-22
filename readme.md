# Sendspin Audio Service for Kodi

`service.sendspin` lets a Linux-based Kodi device, including LibreELEC, play audio from a Sendspin server and appear as a synchronized player. The add-on starts automatically with Kodi and handles audio handoff between Kodi and Sendspin.

> **Alpha software:** This add-on is experimental and may contain bugs or be unstable.

This addon was developed with the assistance of AI.

## What it does

- Makes Kodi available as a Sendspin playback device.
- Keeps Kodi's player display and controls in sync with Sendspin playback.
- Hands the ALSA audio device between Kodi and Sendspin as playback starts, pauses, and stops.

## What you need

- Kodi with support for Python 3 add-ons.
- A Linux-based Kodi device with ALSA audio.
- A Sendspin server, such as Music Assistant.
- Kodi and the server on the same local network for automatic discovery.

The add-on includes native backends for `x86_64`, `i386`, `aarch64`, and `armv7` devices. The `i386` backend is cross-compiled, but has not yet been validated on a 32-bit Kodi device.

## Install

1. Download `service.sendspin.zip` from the [latest stable release](https://github.com/shardshunt/Sendspin-kodi/releases/latest). Avoid a release marked **Pre-release** unless you want to try an unreleased version.
2. In Kodi, open **Add-ons** and choose **Install from zip file**.
3. Select the downloaded ZIP. If Kodi asks, allow installation from unknown sources.
4. Open the add-on settings to configure the connection and playback options below.

The service starts automatically when Kodi starts. Once connected, select the Kodi player from your Sendspin server to begin playback.

## Connect to your Sendspin server

By default, the add-on advertises itself on the local network so a compatible server can discover it. Keep **Sendspin server WebSocket URL (optional)** blank to use discovery.

If discovery is unavailable, enter the server's WebSocket URL in that setting, for example:

```text
ws://192.168.0.10:8927/sendspin
```

Replace the address with the one for your Sendspin server.

## Settings

Open the Sendspin add-on settings in Kodi. Most users only need to change these settings:

### Playback

- **Static playback delay (ms)** — timing adjustment for synchronization, such as compensating for audio processing in an AVR. The default is `0`.
- **Kodi to Sendspin volume scale** — how Kodi's volume maps to Sendspin volume. The default is `0.3`.

### Client

- **Sendspin player name** — the name shown by your server. The default is `Kodi`.
- **Fallback audio device ID or ALSA name** — used when Kodi's current audio device cannot be identified.
- **Activate visualisation window** — show Kodi's music visualization screen during playback.
- **Stop when dummy playback stops** — stop Sendspin playback if Kodi's player stops.

The **Connection** settings and the backend path and audio device override in **Client** are advanced options. Leave them unchanged unless you need to customize a local control API or audio device.

## Troubleshooting

- **Kodi does not appear in the server:** Check that Kodi and the server are on the same local network and that local device discovery is available. You can also enter the server WebSocket URL manually.
- **Playback starts but there is no sound:** Check Kodi's selected audio output and the device's ALSA configuration. If automatic device selection is wrong, try the advanced **Audio device override** setting.
- **Audio is out of sync:** Adjust **Static playback delay (ms)** in small increments.

## Developer information

The remaining sections cover building, deploying, testing, and packaging the add-on.

### Build native backends

The native backend (`sendspin-kodi-backend`) is compiled with CMake, C++20, and ALSA/PortAudio support, using pinned [`sendspin-cpp`](https://github.com/Sendspin/sendspin-cpp) dependencies. Multi-architecture builds are supported for `x86_64`, `i386`, `aarch64`, and `armv7`.

The recommended build uses a containerized Ubuntu 22.04 LTS cross-compilation environment with Podman:

```bash
# Build the full architecture matrix in parallel:
scripts/build_native_backends_all.sh

# Build one architecture for development or device testing:
scripts/build_native_backends_all.sh --arch x86_64

# Build sequentially or limit the number of parallel jobs:
scripts/build_native_backends_all.sh --serial
scripts/build_native_backends_all.sh -j 4

# Build on the host without a container (requires the host toolchain):
scripts/build_native_backends_all.sh --host --arch x86_64
```

The compiled executables are staged under `service.sendspin/resources/bin/<architecture>/`. A single-architecture build is for development and device testing; it does not satisfy the full release matrix check.

### Deploy to a Kodi device

For development deployment to a remote Kodi or LibreELEC box:

```bash
# Upload and restart the service on the default IP (192.168.0.177):
./upload

# Or specify a custom IP:
KODI_IP=192.168.0.x ./upload
```

The script streams the add-on files while preserving remote Python libraries in `resources/lib/`.

### Tests

Install the local Git commit hook once:

```bash
uv run pre-commit install
```

The hook always runs the Python unit tests. It also runs the Kodi and distro
container checks when staged files affect the Kodi add-on, native backend, Kodi
integration tests, or backend build/test setup. Documentation-only changes and
other unrelated files skip those container checks automatically. Run the full
suite manually at any time with:

```bash
scripts/full_test_suite.sh
```

The suite runs Python unit tests, repository-wide pre-commit checks, Kodi API scenarios, and x86_64/i386 backend checks in Ubuntu and Fedora containers. It requires Python 3.11, `uv`, Podman, and network access for container images and distro packages. Use `scripts/full_test_suite.sh --help` for details.

Hardware-dependent playback is not part of the commit hook. To test real audio handoff, run the native Kodi harness on a device with ALSA hardware and a Sendspin server:

```bash
SENDSPIN_SERVER_URL=ws://<server>:8927/sendspin tests/kodi/native_harness.sh
```

The Music Assistant playback test is also available with `tests/kodi/native_playback_test.sh` when its server token and target hardware are configured.

### Implementation notes

When the service starts, it detects the host architecture and launches the matching backend from `resources/bin/<arch>/sendspin-kodi-backend`. The backend advertises Kodi as a Sendspin player using mDNS and the `_sendspin._tcp` service on port `8928` with the `/sendspin` path. If a server WebSocket URL is configured, it connects directly to that address; otherwise it waits for an inbound server connection. On Linux, the backend links to Avahi's client library for DNS-SD advertisement. The Kodi Python service communicates with the backend over a UNIX domain socket (`control.sock`).

#### Audio handoff

To avoid Kodi and ALSA device locking conflicts, the service coordinates playback transitions:

- **Pause:** Return the audio device to Kodi and pause the silent Kodi track.
- **Stop or idle:** After a 2.5-second grace period, return the audio device to Kodi and stop the silent track.
- **Play or resume:** Switch Kodi to a silent alternate output, wait 1.5 seconds for Kodi's audio engine to release the hardware, acquire the device for Sendspin, then wait 0.5 seconds and reset the stream so the server sends fresh audio headers. Track metadata is updated in Kodi without restarting playback.

#### Local control API

The service communicates with the native backend over a local UNIX domain socket. For testing and external integrations, it also provides an HTTP proxy:

- `http://127.0.0.1:59999`
- `POST /control` for playback commands
- `GET /state` for current track, playback, and volume state

See [SENDSPIN_CONTROL_API.md](SENDSPIN_CONTROL_API.md) for the HTTP contract and examples.

### Release and packaging

Before packaging, sync the Python dependencies listed in `pyproject.toml` into the add-on:

```bash
python scripts/get_libs.py
```

Build and validate all native backends before creating a release:

```bash
scripts/build_native_backends_all.sh
python scripts/release.py --check --token GITHUB_TOKEN
```

To create and validate a local ZIP without creating a draft release:

```bash
python scripts/release.py --package
```

The release workflow creates a draft with the ZIP attached. Choose `latest` or `pre-release`, review the generated notes (or supply custom notes), then publish the draft in GitHub. The tag is derived from the add-on version (`v<version>`).

For local release preparation:

```bash
# Create a draft with GitHub-generated notes:
python scripts/release.py --draft --token GITHUB_TOKEN

# Create a pre-release draft or provide custom notes:
python scripts/release.py --draft --release-type pre-release --token GITHUB_TOKEN
python scripts/release.py --draft --notes "Highlights and upgrade notes" --token GITHUB_TOKEN

# Force-create a draft despite non-critical check failures; the full native
# architecture matrix is still required:
python scripts/release.py --draft --force --token GITHUB_TOKEN
```

Plugin versions must follow the `YYYY.Month.Patch` calendar format (for example, `2026.5.0`).
