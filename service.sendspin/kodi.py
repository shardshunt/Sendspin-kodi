import asyncio
import json
import logging
import os
import struct
from collections.abc import Awaitable, Callable

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
        self.app = None
        self.site = None
        self.logger = logging.getLogger("sendspin")

    def _create_wav_header(self, sample_rate: int = 44100, channels: int = 2, bits: int = 16) -> bytes:
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

    async def handle_dummy_audio(self, request: web.BaseRequest) -> web.StreamResponse:
        """
        Aiohttp request handler that streams infinite silence to the client.
        """
        if request.method == "HEAD":
            return web.Response(status=200, headers={"Content-Type": "audio/wav"})

        response = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "audio/wav"})
        await response.prepare(request)

        try:
            header = self._create_wav_header()
            await response.write(header)
            silence = b"\x00" * (44100 * 2 * 2 * 2)
            while self.site is not None:
                await response.write(silence)
                await asyncio.sleep(0.1)
        except (ConnectionResetError, ConnectionError, BrokenPipeError, asyncio.CancelledError):
            self.logger.debug("Kodi disconnected from dummy stream.")
        return response

    async def start(self) -> None:
        """
        Starts the web server background runner.
        """
        self.app = web.Application()
        self.app.router.add_get("/sendspin_dummy.wav", self.handle_dummy_audio)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await self.site.start()

    async def stop(self) -> None:
        """Shuts down the local server by dismantling the site and runner."""
        if self.site:
            await self.site.stop()
            self.site = None
        if self.app:
            await self.app.shutdown()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        self.site = None
        self.app = None


class KodiManager:
    """
    The primary interface for Kodi-specific UI and Volume operations.

    This class abstracts JSON-RPC calls and xbmc built-in functions into
    clean Python methods. It manages the 'dummy playback' used for UI
    persistence and monitors the system for volume changes made by the user.
    """

    def __init__(self, stop_callback: Callable[[], None] = None):
        """
        Initializes the manager and internal state tracking.
        """
        self.logger = logging.getLogger("sendspin")
        self.dummy_server = DummyStreamServer()
        self.player = xbmc.Player(stop_callback=stop_callback)

        # Internal state to prevent feedback loops between Kodi and Server
        self.last_known_volume = -1
        self.last_known_muted = None
        self.volume_monitor_task = None
        self.playback_monitor_task = None
        self.volume_callback = None
        self.stop_callback = stop_callback
        self._is_playing_dummy = False

    async def start(self, on_volume_change: Callable[[int, bool], Awaitable[None]]) -> None:
        """
        Starts the support services for Kodi integration.
        """
        self.volume_callback = on_volume_change
        await self.dummy_server.start()
        self.volume_monitor_task = asyncio.create_task(self._monitor_volume_loop())
        self.playback_monitor_task = asyncio.create_task(self._monitor_playback_loop())

    async def cleanup(self) -> None:
        """
        Stops all background tasks, dummy servers, and UI playback.
        """
        if self.volume_monitor_task:
            self.volume_monitor_task.cancel()
        if self.playback_monitor_task:
            self.playback_monitor_task.cancel()
        await self.dummy_server.stop()
        self.stop_ui()
        await asyncio.sleep(0.5)
        await self.dummy_server.stop()

    # --- UI & Metadata ---

    def update_ui(self, title: str = "Sendspin Stream", artist: str = "Remote Source", thumb: str = "") -> None:
        """
        Updates Kodi's 'Now Playing' information.

        Creates a ListItem with the provided metadata and instructs Kodi
        to 'play' the local dummy stream if it is not already doing so.
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
            self.player.play("http://localhost:9999/sendspin_dummy.wav", list_item)
            self._is_playing_dummy = True

    def stop_ui(self):
        """
        Stops the Kodi player if it is currently playing the dummy stream.
        """
        if self.player.isPlaying():
            get_players_query = {"jsonrpc": "2.0", "method": "Player.GetActivePlayers", "id": 1}
            response_str = xbmc.executeJSONRPC(json.dumps(get_players_query))
            response = json.loads(response_str)
            active_players = response.get("result", [])

            for player in active_players:
                if player.get("type") == "audio":
                    stop_query = {
                        "jsonrpc": "2.0",
                        "method": "Player.Stop",
                        "params": {"playerid": player["playerid"]},
                        "id": 1,
                    }
                    xbmc.executeJSONRPC(json.dumps(stop_query))
                    self.logger.debug(f"Stopped Kodi player {player['playerid']}")
        self._is_playing_dummy = False

    # --- Volume Logic ---

    def get_current_volume(self) -> tuple[int, bool]:
        """
        Retrieves the current application volume via JSON-RPC.
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

    def _get_setting_value(self, setting_id: str) -> str | None:
        """Reads a Kodi setting value using JSON-RPC."""
        try:
            query = {
                "jsonrpc": "2.0",
                "method": "Settings.GetSettingValue",
                "params": {"setting": setting_id},
                "id": 1,
            }
            response_str = xbmc.executeJSONRPC(json.dumps(query))
            response = json.loads(response_str)
            return response.get("result", {}).get("value")
        except Exception as e:
            self.logger.debug(f"Failed to read Kodi setting '{setting_id}': {e}")
            return None

    def _set_setting_value(self, setting_id: str, value: str) -> bool:
        """Sets a Kodi setting value using JSON-RPC."""
        try:
            query = {
                "jsonrpc": "2.0",
                "method": "Settings.SetSettingValue",
                "params": {"setting": setting_id, "value": value},
                "id": 1,
            }
            response_str = xbmc.executeJSONRPC(json.dumps(query))
            response = json.loads(response_str)
            return response.get("result", False) is True
        except Exception as e:
            self.logger.debug(f"Failed to write Kodi setting '{setting_id}': {e}")
            return False

    def get_audio_output_device(self) -> str | None:
        """Returns Kodi's currently selected audio output device."""
        return self._get_setting_value("audiooutput.audiodevice")

    def set_audio_output_device(self, device_name: str) -> bool:
        """Sets Kodi's audio output device."""
        self.logger.info(f"Switching Kodi audio output device to: {device_name}")
        return self._set_setting_value("audiooutput.audiodevice", device_name)

    def set_volume(self, volume: int = None, muted: bool = None) -> None:
        """
        Sets the Kodi system volume and/or mute state.

        This method updates the internal state tracking before executing
        the command to ensure the monitor loop doesn't treat this as a
        new local user action (preventing feedback loops).
        """
        if volume is not None:
            self.last_known_volume = int(volume)
            xbmc.executebuiltin(f"SetVolume({int(volume)})")

        if muted is not None:
            self.last_known_muted = bool(muted)
            state_str = "true" if muted else "false"
            xbmc.executebuiltin(f"SetMute({state_str})")

    async def _monitor_playback_loop(self) -> None:
        """
        Background loop that polls for playback state changes.
        """
        self.logger.info("Kodi Playback monitor started.")
        while not xbmc.Monitor().abortRequested():
            try:
                # Check if we think we are playing, but the Kodi player says no.
                is_actually_playing = self.player.isPlaying()
                if self._is_playing_dummy and not is_actually_playing:
                    self.logger.info("Playback monitor detected playback has stopped.")
                    self._is_playing_dummy = False
                    if self.stop_callback:
                        self.stop_callback()
                # Sync our internal state if it's out of sync for any other reason
                elif not self._is_playing_dummy and is_actually_playing:
                    self.logger.info("Playback monitor detected playback has started externally.")
                    self._is_playing_dummy = True

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in playback monitor: {e}")

            await asyncio.sleep(1)  # Poll every second

    async def _monitor_volume_loop(self) -> None:
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
