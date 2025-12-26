# Sendspin Service for Kodi

This is a background service for Kodi that acts as a client for the Sendspin audio streaming server. It allows Kodi to play audio from a Sendspin server.

## Disclaimer

This addon is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## How it Works

The service connects to a Sendspin server using a WebSocket connection. When an audio stream begins, the addon starts a local web server on your Kodi device. This server provides the incoming audio as a `.wav` stream.

The addon then tells the Kodi player to play this local stream, effectively acting as a proxy for the Sendspin audio. This allows for seamless integration with Kodi's native player.

## Installation

### 1. Download Dependencies

The addon requires several Python libraries to function. A helper script is included to download them into the correct folder.

**Prerequisites:** You must have `Python 3.11` and `pip` installed on your system.


Run the script:
```
python3 /service.sendspin/tools/get_libs.py
```
This will install the dependencies into the `service.sendspin/resources/lib` folder.

### 2. Package the Addon

After the dependencies are downloaded, you need to create a zip file of the `service.sendspin` directory contents.


Zip the `service.sendspin` directory.

### 3. Install in Kodi

1.  Open Kodi.
2.  Go to **Settings** (the gear icon).
3.  Select **Add-ons**.
4.  Select **Install from zip file**.
5.  Navigate to the location where you saved `service.sendspin.zip`.
6.  Select the zip file to install it.
7.  Wait for the "Add-on installed" notification.


## Configuration

The addon can be configured through its settings in Kodi.

-   **Server WebSocket URL**: The address of the Sendspin server to connect to (e.g., `ws://192.168.1.100:8927/sendspin`).
-   **Local proxy port**: The local TCP port the addon will use for its internal web server (default is `59999`). You shouldn't need to change this unless it conflicts with another service.
-   **Client ID**: A unique identifier for this Kodi client.
-   **Client Name**: A friendly name to identify this client on the Sendspin server.
-   **Log file path**: The location to store the addon's log file.
-   **Startup error file**: A file to log any critical errors that happen when the service first starts.


# Sendspin Kodi Service - Implementation TODO

## 1. Clock Synchronization
- [ ] Implement `client/time` background loop to send client timestamps.
- [ ] Handle `server/time` responses to calculate local clock offset.
- [ ] Use big-endian timestamps from Type 4 binary chunks to sync audio playback.

## 2. Protocol Handshake & State
- [ ] Update `client/hello` to include `version: 1` and `device_info`.
- [ ] Wait for `server/hello` before sending subsequent messages.
- [ ] Send initial `client/state` (volume/mute) immediately after handshake.

## 3. Command & Event Handling
- [ ] Add listener for `stream/clear` to flush Kodi playback buffers.
- [ ] Implement `client/goodbye` on service shutdown with reason codes.
- [ ] Send `client/state` updates to server when local Kodi volume changes.

## 4. UI & Group Logic
- [ ] Implement `group/update` listener to sync with group `playback_state`.
- [ ] Calculate and display track progress using the protocol formula.

## 5. Optional Enhancements
- [ ] Implement `Artwork` role for high-quality binary image transfers.
- [ ] Add `stream/request-format` logic for dynamic codec switching (e.g., FLAC).
