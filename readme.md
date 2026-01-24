# Sendspin Service for Kodi

This is a background service for Kodi that acts as a client for the Sendspin audio streaming server. It allows Kodi to play audio from a Sendspin server.

## Disclaimer

This addon is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## How it Works

TODO

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

### 3 . Ensure kodi is Using PulseAudio

For this addon to mix audio correctly, Kodi must use the PulseAudio backend.
1. Navigate to Settings > System > Audio.
2. Check the Audio output device setting.
3. Ensure the selected device name begins with PULSE: (e.g., PULSE: Default).

#### Troubleshooting: No "PULSE" Devices Available

If you only see devices starting with ALSA: and no PULSE: options are listed, Kodi likely has an exclusive lock on the hardware. Follow these steps to fix it:

1. Connect via SSH to your LibreELEC device.
2. Stop Kodi to release the hardware lock:
```
systemctl stop kodi
```
3. Force PulseAudio to detect your hardware modules:
```
pactl load-module module-udev-detect
```
4. Restart Kodi:
```
systemctl start kodi
```
5. Return to Settings > System > Audio in Kodi. You should now see a PULSE: device corresponding to your hardware. Select it to finish setup.

### 4. Install in Kodi

1.  Open Kodi.
2.  Go to **Settings** (the gear icon).
3.  Select **Add-ons**.
4.  Select **Install from zip file**.
5.  Navigate to the location where you saved `service.sendspin.zip`.
6.  Select the zip file to install it.
7.  Wait for the "Add-on installed" notification.

## Configuration

The addon can be configured through its settings in Kodi.

-   **Client ID**: A unique identifier for this Kodi client.
-   **Client Name**: A friendly name to identify this client on the Sendspin server.
-   **Log file path**: The location to store the addon's log file.
-   **Startup error file**: A file to log any critical errors that happen when the service first starts.

# Sendspin Kodi Service - Implementation TODO

- [ ] **Handle Stream End**: Create an `on_stream_end` method in `AudioProxy` to stop Kodi playback when a `stream/end` message is received. Register it with `set_stream_end_listener`.
- [ ] **Handle Stream Clear**: Create an `on_stream_clear` method to handle buffer clearing (e.g., on seek). This should stop or restart playback. Register it with `set_stream_clear_listener`.
- [ ] **Handle Server Commands**: Create an `on_server_command` method to process direct commands from the server (e.g., volume/mute). Register it with `set_server_command_listener`.
- [ ] **Handle Disconnection**: Create an `on_disconnect` method to clean up the player state immediately when the connection is lost. Register it with `set_disconnect_listener`.
- [ ] **Handle Group Updates**: Create an `on_group_update` method to manage multi-room synchronization events. Start by logging the event data. Register it with `set_group_update_listener`.
- [ ] **Review Existing TODOs**: Address the `TODO` comments in `on_stream_start` and `on_controller_state` to improve the existing handlers.