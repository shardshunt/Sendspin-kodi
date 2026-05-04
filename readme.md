# Sendspin Service for Kodi

This is a background service for Kodi that acts as a client for the Sendspin audio streaming server. It works by launching and managing a lightweight Docker container to handle high-fidelity audio playback directly via ALSA hardware.

## Disclaimer

This addon is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## How it Works

Instead of running the audio processing libraries inside Kodi's Python environment, this addon delegates playback to a dedicated Sendspin Docker container (`sendspin-local`).

When activated, the addon:
1. Detects the physical ALSA hardware device currently being used by Kodi.
2. Temporarily shifts Kodi to a fallback audio sink to release the hardware lock.
3. Launches the Docker container, passing it the exact ALSA hardware index via `/dev/snd`.
4. Restores Kodi's original audio settings when playback is stopped.

## Requirements

- **Docker**: The host system must have Docker installed and running (e.g., Docker Addon for LibreELEC).

## Installation

Because this addon relies entirely on Docker for playback, there are no external Python dependencies to download.

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
