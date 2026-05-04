# Sendspin Service for Kodi

This is music provider for Kodi that acts as a client for the Sendspin audio streaming server. It works by launching and managing a lightweight Docker container running [Sendspin-CLI](https://github.com/Sendspin/sendspin-cli) to handle audio playback and synchronisation.

## Disclaimer

This addon is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## Usage

Sendspin presents as a music provider, when run it starts a stream and the docker container which can be used as per sendspin cli.

Currently client side control must be done via `docker exec sendspin-player` commands

## How it Works

When activated, the addon:

1. Detects the audio hardware device currently being used by Kodi.
2. Temporarily shifts Kodi to a fallback audio sink to release the hardware lock.
3. Launches the Docker container, passing it the exact ALSA hardware index via `/dev/snd`.
4. Restores Kodi's original audio settings when playback is stopped.

## Requirements

- **Docker**: The host system must have Docker installed  and avalible in system `$PATH` (e.g., Docker Addon for LibreELEC).


## Installation

### 1. Package the Addon
Create a zip file of the `service.sendspin` directory contents.

### 2. Install in Kodi
1. Open Kodi.
2. Go to **Settings** (the gear icon).
3. Select **Add-ons**.
4. Select **Install from zip file**.
5. Navigate to the location where you saved `service.sendspin.zip`.
6. Select the zip file to install it.
7. Wait for the "Add-on installed" notification.

## Configuration

The addon can be configured through its settings in Kodi.

**Connection & Client:**
- **Server WebSocket URL**: The address of your main Sendspin server.
- **Client ID & Name**: Identifiers for this Kodi client on your network.

**Docker & Audio Settings:**
- **Docker container name**: Name for the spawned container (default: `sendspin-player`).
- **Docker image name**: The image to run (default: `sendspin-local`).
- **Docker config directory**: The mapped path for container settings (default: `/storage/.config/sendspin`).
- **Audio device ID override**: Force the container to use a specific ALSA index (leave empty to auto-detect).
- **Fallback audio device ID**: The ALSA index to use if Kodi is currently using PulseAudio (e.g., Bluetooth) and hardware auto-detection fails.

**Logging:**
- **Log file path**: The location to store the addon's log file. Container logs are streamed directly into Kodi's main log.
- **Startup error file**: A file to log any critical errors that happen when the service first starts.
