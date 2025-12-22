# Sendspin Service for Kodi

This is a background service for Kodi that acts as a client for the Sendspin audio streaming server. It allows Kodi to play audio from a Sendspin server.

## Disclaimer

This addon is in an ALPHA state. It is experimental and may contain bugs or be unstable. Use it at your own risk.

This addon was developed with the assistance of AI.

## How it Works

The service connects to a Sendspin server using a WebSocket connection. When an audio stream begins, the addon starts a local web server on your Kodi device. This server provides the incoming audio as a `.wav` stream.

The addon then tells the Kodi player to play this local stream, effectively acting as a proxy for the Sendspin audio. This allows for use of Kodi's native player. 


## Configuration

The addon can be configured through its settings in Kodi.

-   **Server WebSocket URL**: The address of the Sendspin server to connect to (e.g., `ws://192.168.1.100:8927/sendspin`).
-   **Local proxy port**: The local TCP port the addon will use for its internal web server (default is `59999`). You shouldn't need to change this unless it conflicts with another service.
-   **Client ID**: A unique identifier for this Kodi client.
-   **Client Name**: A friendly name to identify this client on the Sendspin server.
-   **Log file path**: The location to store the addon's log file.
-   **Startup error file**: A file to log any critical errors that happen when the service first starts.
