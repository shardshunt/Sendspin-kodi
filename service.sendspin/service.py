#!/usr/bin/env python3
"""
Sendspin Service for Kodi.

Uses a Docker-based Sendspin backend for playback and manages Kodi integration.
"""

# system imports
import os
import sys
import time
import traceback

# adjust sys.path for embedded Kodi environment
if (
    os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))
    and os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib") not in sys.path
):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# standard library imports
import asyncio
import logging

import logger
import xbmcaddon

# aiosendspin imports
from aiosendspin.client import AudioFormat
from aiosendspin.models.core import ServerCommandPayload, ServerStatePayload, StreamEndMessage, StreamStartMessage
from aiosendspin.models.types import PlaybackStateType, PlayerCommand
from audio import DockerPlaybackEngine
from kodi import KodiManager

import xbmc


class ThrottledLogger:
    """Helper to prevent log flooding during high-frequency events."""

    def __init__(self, interval: int = 1) -> None:
        self.interval = interval
        self.last_log_time = 0

    def log(self, message: str) -> None:
        current_time = time.time()
        if current_time - self.last_log_time >= self.interval:
            xbmc.log(f"[Sendspin-Debug] {message}", level=xbmc.LOGDEBUG)
            self.last_log_time = current_time


throttled_log = ThrottledLogger()

# --- CONFIGURATION & UTILITIES ---

CLIENT_ID = xbmcaddon.Addon().getSetting("client_id") or "kodi-sendspin-client"
CLIENT_NAME = "Kodi"
BUFFERSIZE_REQUEST_MS = 5000  # 5 seconds


class SendspinServiceController:
    """
    Main Service Controller.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("sendspin")
        self.playback_engine = DockerPlaybackEngine(
            image_name=xbmcaddon.Addon().getSetting("docker_image_name") or "sendspin-local",
            container_name=xbmcaddon.Addon().getSetting("docker_container_name") or "sendspin-player",
            config_dir=xbmcaddon.Addon().getSetting("docker_config_dir") or "/storage/.config/sendspin",
        )
        self.kodi = KodiManager(stop_callback=self.handle_stop)

        # Audio Configuration
        self.sample_rate_max = 48000
        self.channels = 2
        self.bit_depth = 16
        self.buffer_bytes = int(
            (BUFFERSIZE_REQUEST_MS / 1000.0) * self.sample_rate_max * self.channels * (self.bit_depth // 8)
        )
        self.is_playing = False
        self.playback_state = PlaybackStateType.STOPPED

    async def setup(self) -> None:
        """Initial setup and connection to Sendspin server."""

        current_vol, current_mute = self.kodi.get_current_volume()

        # Start the Docker-based playback backend only.
        self.logger.info("Starting Docker Sendspin backend container.")
        self.playback_engine.start()

    async def run(self) -> None:
        """Main execution loop."""
        await self.setup()
        await self.kodi.start(on_volume_change=self.handle_local_volume_change)
        monitor = xbmc.Monitor()
        while not monitor.abortRequested():
            await asyncio.sleep(1)
        await self.cleanup()

    async def cleanup(self) -> None:
        """Clean shutdown. TODO: Needs work."""
        self.playback_engine.stop()
        await self.kodi.cleanup()
        self.logger.info("Shutting down Sendspin service...")

    # --- Kodi Event Handlers  ---

    async def handle_local_volume_change(self, volume: int, muted: bool) -> None:
        """Called by KodiManager when the user changes volume locally."""
        self.logger.debug(f"Syncing local volume: Vol={volume}, Mute={muted}")

        self.logger.debug("Docker backend active; local volume changes are managed by the container or PulseAudio.")
        # The Docker Sendspin playback service should handle audio level/mute.

    def handle_stop(self) -> None:
        """Stop handler triggered by Kodi Player events."""
        self.logger.info("Playback stop detected.")
        if self.playback_state == PlaybackStateType.STOPPED:
            return
        self.is_playing = False
        self.playback_state = PlaybackStateType.STOPPED
        self.playback_engine.stop()
        self.kodi.stop_ui()

    # --- Sendspin Event Handlers ---
    def on_stream_start(self, message: StreamStartMessage) -> None:
        """Triggered when Sendspin starts a stream."""
        self.logger.info(
            f"Stream Start Received. Sample Rate: {message.payload.player.sample_rate}, Channels: {message.payload.player.channels}, Bit Depth: {message.payload.player.bit_depth}"
        )
        self.is_playing = True
        self.playback_state = PlaybackStateType.PLAYING

    def on_audio_chunk(self, server_timestamp_us: int, audio_data: bytes, audio_format: AudioFormat) -> None:
        """Handles incoming audio data chunks."""
        # Docker backend does not consume local audio chunks.
        return

    def on_metadata_update(self, payload: ServerStatePayload) -> None:
        """Called when track info (Artist/Title/Art) changes."""
        metadata = getattr(payload, "metadata", {})

        title = getattr(metadata, "title", "Unknown")
        artist = getattr(metadata, "artist", "Unknown")

        self.logger.info(f"Metadata Update: {artist} - {title}")
        if isinstance(title, str) and isinstance(artist, str) and self.is_playing:
            self.kodi.update_ui(title="Sendspin Stream", artist="Sendspin Stream")

    def on_stream_end(self, message: StreamEndMessage) -> None:
        """Triggered when stream ends. TODO: Needs work."""
        self.logger.info("Stream End received")
        self.handle_stop()

    def on_server_command(self, payload: ServerCommandPayload) -> None:
        """Handle Volume/Mute commands from the Sendspin server."""
        command_data = getattr(payload, "player", None)
        self.logger.debug(f"Server Command received: {command_data.command}")

        if command_data.command == PlayerCommand.VOLUME:
            vol = getattr(command_data, "volume", None)
            muted = getattr(command_data, "muted", None)

            if vol is not None:
                self.playback_engine.set_volume(vol)
            if muted is not None:
                self.playback_engine.set_mute(muted)

            # Update Kodi UI
            self.kodi.set_volume(vol, muted)


# --- Entry Point ---

if __name__ == "__main__":
    # Initialize Logger
    log = logger.init_logger()
    log.info("Sendspin Service Starting.")

    # Run Async Loop
    service = SendspinServiceController()
    try:
        asyncio.run(service.run())
    except Exception:
        log.exception("Unhandled exception in sendspin service")
        traceback.print_exc()
