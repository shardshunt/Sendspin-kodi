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
```sh
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

#### fix no pulse audio for libreElec

1. Create directories and upload the script and service files:
```sh
mkdir -p /storage/.config/pulseaudio-fix
```
2. Place the script at `/storage/.config/pulseaudio-fix/wait-for-pulse-sink.sh` 
3. Place the unit at `/storage/.config/system.d/pulseaudio-fix.service`.
4. Make the script executable:
```sh
chmod +x /storage/.config/pulseaudio-fix/wait-for-pulse-sink.sh
```
5. Reload systemd daemon and enable the service:
```sh
systemctl daemon-reload
systemctl enable pulseaudio-fix.service
```
6. Reboot the device to test full boot ordering:
```sh
reboot
```

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