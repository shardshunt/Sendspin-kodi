#!/usr/bin/env python3
"""
Sendspin Service for Kodi.

This service runs in the background, connects to a Sendspin server via WebSocket,
advertises itself via mDNS, and streams received PCM audio to the local hardware
using PulseAudio.
"""

# system imports
import os, sys, traceback, time

# setup module paths
ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR_LIB = os.path.join(ADDON_ROOT, "resources", "lib")
if os.path.isdir(VENDOR_LIB) and VENDOR_LIB not in sys.path:
    sys.path.insert(0, VENDOR_LIB)
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

# standard library imports
import asyncio, logging, subprocess, struct
import xbmc, xbmcgui, xbmcaddon
from audio import AudioRouter, SyncPlaybackEngine
from kodi import KodiManager
from aiohttp import web

# aiosendspin imports
from aiosendspin.client import SendspinClient, ClientListener
from aiosendspin.models.types import Roles, AudioCodec, PlayerCommand, UndefinedField, PlaybackStateType, PlayerStateType
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.core import StreamStartMessage, ServerCommandPayload, ServerStatePayload

# Throttled logger for debug
import logger
class ThrottledLogger:
    """Helper to prevent log flooding during high-frequency events."""
    def __init__(self, interval=0.5):
        self.interval = interval
        self.last_log_time = 0

    def log(self, message):
        current_time = time.time()
        if current_time - self.last_log_time >= self.interval:
            xbmc.log(f"[Sendspin-Debug] {message}", level=xbmc.LOGDEBUG)
            self.last_log_time = current_time
throttledLog = ThrottledLogger(1)

# --- CONFIGURATION & UTILITIES ---

CLIENT_ID = xbmcaddon.Addon().getSetting("client_id") or "kodi-sendspin-client"
CLIENT_NAME = "Kodi"
BUFFERSIZE_REQUEST_MS = 3000 # 3 seconds

class SendspinServiceController:
    """
    Main Service Controller.
    
    Orchestrates the Sendspin client, the audio router, and the playback engine.
    Handles network events and routes audio data to the player.
    """
    def __init__(self):
        self.logger = logging.getLogger("sendspin")
        self.addon = xbmcaddon.Addon()
        self.engine = SyncPlaybackEngine()
        self.router = AudioRouter()
        self.kodi = KodiManager()
        self.client: SendspinClient = None

        # Audio Configuration
        self.sample_rate_max = 48000
        self.channels = 2
        self.bit_depth = 16
        self.buffer_bytes = int((BUFFERSIZE_REQUEST_MS / 1000.0) * self.sample_rate_max * self.channels * (self.bit_depth // 8))
        self.is_playing = False
        self.playback_state = PlaybackStateType.STOPPED
    
    async def setup(self):
        """Registers listeners and starts the Client Listener."""

        current_vol, current_mute = self.kodi.get_current_volume()

        # Sendspin Player Support Declaration
        self.player_support = ClientHelloPlayerSupport(
            supported_formats = [ 
                # SupportedAudioFormat(
                #     AudioCodec.PCM,
                #     channels=2,
                #     sample_rate=48000,
                #     bit_depth=16,
                # ),
                SupportedAudioFormat(
                    AudioCodec.PCM,
                    channels=2,
                    sample_rate=44100,
                    bit_depth=16,
                )
            ],
            buffer_capacity = self.buffer_bytes,
            supported_commands= [ PlayerCommand.VOLUME ],
        )

        #initialize client
        self.client = SendspinClient(
            client_id=CLIENT_ID,
            client_name=CLIENT_NAME,
            roles=[Roles.PLAYER, Roles.METADATA],
            player_support=self.player_support,
            static_delay_ms=-150,
            initial_volume=current_vol,
            initial_muted=current_mute,
        )

        self.engine.set_time_provider(
            self.client.compute_play_time, 
            self.client.is_time_synchronized
        )

        handlers = {
            "add_stream_start_listener": self.on_stream_start,
            "add_stream_end_listener": self.on_stream_end,
            "add_server_command_listener": self.on_server_command,
            "add_metadata_listener": self.on_metadata_update,
        }
        logger.setup_client_listeners(self.client, handlers, log=self.logger, mode="all",exclude="add_audio_chunk_listener")
        self.client.add_audio_chunk_listener(self.on_audio_chunk)

        async def handle_incoming_connection(ws):
            await self.client.attach_websocket(ws)
            info = self.client.server_info
            self.logger.info(f"Connected to Sendspin server. Name: {info.name} Inital Volume: {current_vol}")
            done = asyncio.Event()
            self.client.add_disconnect_listener(done.set)
            await done.wait()

        self.listener = ClientListener(
            client_id=CLIENT_ID,
            on_connection=handle_incoming_connection,
            advertise_mdns=True
        )
        self.logger.info("Starting Sendspin listener.")  
        await self.listener.start()
    

    async def run(self):
        """Main execution loop."""
        await self.setup()
        await self.kodi.start(on_volume_change=self.handle_local_volume_change)
        monitor = xbmc.Monitor()
        while not monitor.abortRequested():
            await asyncio.sleep(1)
        await self.cleanup()

    async def cleanup(self):
        """Clean shutdown."""
        self.engine.stop()
        self.router.cleanup()
        if self.client:
            await self.client.disconnect()
        self.logger.info("Shutting down Sendspin service...")

    # --- Kodi Event Handlers  ---

    async def handle_local_volume_change(self, volume, muted):
        """Called by KodiManager when the user changes volume locally."""
        self.logger.debug(f"Syncing local volume: Vol={volume}, Mute={muted}")
        
        # Update Audio Engine
        self.engine.set_volume(volume)
        self.engine.set_mute(muted)

        # Push to Server
        if self.client:
            await self.client.send_player_state(
                state=PlayerStateType.SYNCHRONIZED,
                volume=volume,
                muted=muted
            )

    # --- Sendspin Event Handlers ---
    def on_stream_start(self, message: StreamStartMessage):
        """Triggered when Sendspin starts a stream."""
        self.logger.info(f"Stream Start Received. Sample Rate: {message.payload.player.sample_rate}, Channels: {message.payload.player.channels}, Bit Depth: {message.payload.player.bit_depth}")
        self.is_playing = True

        virtual_sink = self.router.setup_routing()
        self.engine.start(
            rate = message.payload.player.sample_rate,
            channels = message.payload.player.channels,
            bit_depth = message.payload.player.bit_depth,
            target_sink = virtual_sink
        )
        self.playback_state = PlaybackStateType.PLAYING
        vol, muted = self.kodi.get_current_volume()
        self.engine.set_volume(vol)
        self.engine.set_mute(muted)
        asyncio.create_task(
            self.client.send_player_state(
                state=PlayerStateType.SYNCHRONIZED, 
                volume=vol, muted=muted
        ))

    def on_audio_chunk(self, server_timestamp_us: int, audio_data: bytes, audio_format):
        """Handles incoming audio data chunks."""
        self.engine.play_chunk(server_timestamp_us, audio_data)
    
    def on_metadata_update(self, payload: ServerStatePayload):
        """Called when track info (Artist/Title/Art) changes."""
        metadata = getattr(payload, 'metadata', {})

        title = getattr(metadata, 'title', 'Unknown')
        artist = getattr(metadata, 'artist', 'Unknown')
        thumb = getattr(metadata, 'artwork_url', '')
        
        self.logger.info(f"Metadata Update: {artist} - {title}")
        if isinstance(title, str) and isinstance(artist, str) and self.is_playing:
            self.kodi.update_ui(title="Sendspin Stream", artist="Sendspin Stream")

    def on_stream_end(self, roles=None):
        """Triggered when stream ends."""
        self.logger.info("Stream End received")
        self.is_playing = False
        self.playback_state = PlaybackStateType.STOPPED
        asyncio.create_task(self._async_stop_sequence())
    
    async def _async_stop_sequence(self):
        self.kodi.stop_ui()
        await asyncio.sleep(4)
        self.engine.stop()

    def on_server_command(self, payload: ServerCommandPayload):
        """Handle Volume/Mute commands from the Sendspin server."""
        command_data = getattr(payload, 'player', None)
        self.logger.debug(f"Server Command received: {command_data.command}")

        if command_data.command == PlayerCommand.VOLUME:
            vol = getattr(command_data, 'volume', None)
            muted = getattr(command_data, 'muted', None)

            if vol is not None: self.engine.set_volume(vol)
            if muted is not None: self.engine.set_mute(muted)
            
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
