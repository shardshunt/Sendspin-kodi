import asyncio
import json
import logging
import os
import struct

import xbmcaddon
import xbmcgui
from aiohttp import web

import xbmc


class DummyStreamServer:
    """
    Hosts a local HTTP server streaming silent WAV audio.

    This server acts as a 'virtual source' for Kodi's internal player. By
    streaming continuous silence, we trick Kodi into staying in a 'Playing'
    state. This allows the Sendspin service to:
    1. Keep the music visualization or 'Now Playing' UI visible.
    2. Prevent the system from engaging screensavers or power-off timers.
    3. Maintain a consistent UI context for the user.
    """

    def __init__(self, port=9999):
        """
        Initialize the dummy server settings.

        Args:
            port (int): The local port to host the dummy stream on.
        """
        self.port = port
        self.runner = None
        self.logger = logging.getLogger("sendspin")

    def _create_wav_header(self, sample_rate=44100, channels=2, bits=16):
        """
        Generates a standard RIFF/WAVE header for a PCM stream.

        Kodi requires a valid header to identify the stream format even if
        the data is infinite.
        """
        header = b"RIFF"
        header += struct.pack("<I", 0xFFFFFFFF)  # File size (unknown/infinite)
        header += b"WAVE"
        header += b"fmt "
        header += struct.pack("<I", 16)  # Subchunk size
        header += struct.pack("<H", 1)  # Audio format (PCM)
        header += struct.pack("<H", channels)
        header += struct.pack("<I", sample_rate)
        header += struct.pack("<I", sample_rate * channels * bits // 8)
        header += struct.pack("<H", channels * bits // 8)
        header += struct.pack("<H", bits)
        header += b"data"
        header += struct.pack("<I", 0xFFFFFFFF)  # Data size (unknown/infinite)
        return header

    async def handle_dummy_audio(self, request):
        """
        Aiohttp request handler that streams infinite silence to the client.
        """
        response = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "audio/basic"})
        await response.prepare(request)

        try:
            header = self._create_wav_header()
            await response.write(header)
            # Create a 1-second chunk of silence
            silence = b"\x00" * (44100 * 2 * 2 * 2)
            while True:
                await response.write(silence)
                await asyncio.sleep(0.1)
        except (ConnectionResetError, ConnectionError, BrokenPipeError, asyncio.CancelledError):
            pass
        return response

    async def start(self):
        """
        Starts the web server background runner.
        """
        app = web.Application()
        app.router.add_get("/sendspin_dummy", self.handle_dummy_audio)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await site.start()

    async def stop(self):
        """
        Gracefully shuts down the web server.
        """
        if self.runner:
            await self.runner.cleanup()


class KodiManager:
    """
    The primary interface for Kodi-specific UI and Volume operations.

    This class abstracts JSON-RPC calls and xbmc built-in functions into
    clean Python methods. It manages the 'dummy playback' used for UI
    persistence and monitors the system for volume changes made by the user.
    """

    def __init__(self):
        """
        Initializes the manager and internal state tracking.
        """
        self.logger = logging.getLogger("sendspin")
        self.dummy_server = DummyStreamServer()
        self.player = xbmc.Player()

        # Internal state to prevent feedback loops between Kodi and Server
        self.last_known_volume = -1
        self.last_known_muted = None
        self.monitor_task = None
        self.volume_callback = None

    async def start(self, on_volume_change):
        """
        Starts the support services for Kodi integration.

        Args:
            on_volume_change (callable): An async function called when a
                local volume change is detected. Receives (volume, muted).
        """
        self.volume_callback = on_volume_change
        await self.dummy_server.start()
        self.monitor_task = asyncio.create_task(self._monitor_volume_loop())

    async def cleanup(self):
        """
        Stops all background tasks, dummy servers, and UI playback.
        """
        if self.monitor_task:
            self.monitor_task.cancel()
        await self.dummy_server.stop()
        self.stop_ui()

    # --- UI & Metadata ---

    def update_ui(self, title="Sendspin Stream", artist="Remote Source", thumb=""):
        """
        Updates Kodi's 'Now Playing' information.

        Creates a ListItem with the provided metadata and instructs Kodi
        to 'play' the local dummy stream if it is not already doing so.

        Args:
            title (str): Track or stream title.
            artist (str): Performer or source name.
            thumb (str): Optional path or URL to an album art image.
        """
        list_item = xbmcgui.ListItem(title)
        info_tag = list_item.getMusicInfoTag()
        info_tag.setTitle(title)
        info_tag.setArtist(artist)

        if thumb:
            list_item.setArt({"thumb": thumb})
        else:
            addon_path = xbmcaddon.Addon().getAddonInfo("path")
            icon = os.path.join(addon_path, "icon.png")
            list_item.setArt({"thumb": icon})

        if not self.player.isPlaying():
            self.logger.info("Starting dummy playback for UI")
            self.player.play("http://localhost:9999/sendspin_dummy", list_item)

    def stop_ui(self):
        """
        Stops the Kodi player if it is currently playing the dummy stream.
        """
        if self.player.isPlaying():
            self.player.stop()

    # --- Volume Logic ---

    def get_current_volume(self):
        """
        Retrieves the current application volume via JSON-RPC.

        Returns:
            tuple: (int volume, bool muted) where volume is 0-100.
        """
        try:
            query = {
                "jsonrpc": "2.0",
                "method": "Application.GetProperties",
                "params": {"properties": ["volume", "muted"]},
                "id": 1,
            }
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response).get("result", {})
            return result.get("volume", 100), result.get("muted", False)
        except Exception as e:
            self.logger.debug(f"Failed to get properties via JSON-RPC: {e}")
            return 100, False

    def set_volume(self, volume=None, muted=None):
        """
        Sets the Kodi system volume and/or mute state.

        This method updates the internal state tracking before executing
        the command to ensure the monitor loop doesn't treat this as a
        new local user action (preventing feedback loops).

        Args:
            volume (int, optional): New volume level (0-100).
            muted (bool, optional): New mute state.
        """
        if volume is not None:
            self.last_known_volume = int(volume)
            xbmc.executebuiltin(f"SetVolume({int(volume)})")

        if muted is not None:
            self.last_known_muted = bool(muted)
            state_str = "true" if muted else "false"
            xbmc.executebuiltin(f"SetMute({state_str})")

    async def _monitor_volume_loop(self):
        """
        Background loop that polls for manual volume changes in Kodi.

        If the user uses a remote, keyboard, or slider to change volume,
        this loop detects it and triggers the 'volume_callback' to notify
        the server and the software audio engine.
        """
        self.logger.info("Kodi Volume monitor started.")
        while not xbmc.Monitor().abortRequested():
            try:
                current_vol, current_mute = self.get_current_volume()

                # Check if the change originated from the Kodi UI/Hardware
                vol_changed = self.last_known_volume != -1 and current_vol != self.last_known_volume
                mute_changed = self.last_known_muted is not None and current_mute != self.last_known_muted

                if vol_changed or mute_changed:
                    self.last_known_volume = current_vol
                    self.last_known_muted = current_mute

                    if self.volume_callback:
                        asyncio.create_task(self.volume_callback(current_vol, current_mute))

                # Initial sync to baseline
                if self.last_known_volume == -1:
                    self.last_known_volume = current_vol
                    self.last_known_muted = current_mute

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in volume monitor: {e}")

            await asyncio.sleep(0.5)
