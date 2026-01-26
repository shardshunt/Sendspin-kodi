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
BUFFERSIZE_REQUEST_MS = 2000 # 3 seconds
SINK_LATENCY_MS = 50 # 50ms * 3 = 150ms total buffer

# --- MAIN CLASSES ---

class KodiMetadataHandler:
    def __init__(self):
        self.player = xbmc.Player()
        self.active_item = None
        self.logger = logging.getLogger("sendspin")

    def update_metadata(self, title="Sendspin Stream", artist="Remote Source", album="", thumb=""):
        """Creates or updates the Kodi UI with current track info."""
        # 1. Create the list item
        list_item = xbmcgui.ListItem(title)
        
        # 2. Set Music Info
        info_tag = list_item.getMusicInfoTag()
        info_tag.setTitle(title)
        info_tag.setArtist(artist)
        
        # 3. Set Art
        if thumb:
            list_item.setArt({'thumb': thumb})
        else:
            # Fallback to addon icon
            addon_path = xbmcaddon.Addon().getAddonInfo('path')
            icon = os.path.join(addon_path, 'icon.png')
            list_item.setArt({'thumb': icon})

        self.active_item = list_item

        # 4. Trigger "Playback" to show the UI
        # We use a dummy URL. 'special://temp/dummy.mp3' works well as a placeholder.
        # This makes Kodi think it's playing, which opens the Music Viz / Now Playing screen.
        if not self.player.isPlaying():
            self.logger.info("Starting dummy playback for UI")
            self.player.play("http://localhost:9999/sendspin_dummy", list_item)

    def stop(self):
        """Stops the Kodi UI player."""
        if self.player.isPlaying():
            self.player.stop()

class DummyStreamServer:
    def __init__(self, port=9999):
        self.port = port
        self.runner = None
        self.logger = logging.getLogger("sendspin")

    def _create_wav_header(self, sample_rate=44100, channels=2, bits=16):
        """Generates a standard WAV header with 'infinite' length."""
        header = b'RIFF'
        header += struct.pack('<I', 0xFFFFFFFF)  # Chunk size (max 32-bit int for streaming)
        header += b'WAVE'
        header += b'fmt '
        header += struct.pack('<I', 16)  # PCM chunk size
        header += struct.pack('<H', 1)   # Audio Format (1 = PCM)
        header += struct.pack('<H', channels)
        header += struct.pack('<I', sample_rate)
        header += struct.pack('<I', sample_rate * channels * bits // 8) # Byte rate
        header += struct.pack('<H', channels * bits // 8) # Block align
        header += struct.pack('<H', bits) # Bits per sample
        header += b'data'
        header += struct.pack('<I', 0xFFFFFFFF) # Data size (max 32-bit int)
        
        return header

    async def handle_dummy_audio(self, request):
        """Returns an infinite stream of silent PCM data."""
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={'Content-Type': 'audio/basic'} # or audio/l16
        )
        await response.prepare(request)

        try:
            # 1. Send the WAV Header first
            header = self._create_wav_header()
            await response.write(header)
            
            # 2. Send silence continuously
            silence = b'\x00' * (44100 * 2 * 2 *2 ) # 2 seconds of silence at 44.1kHz, 16-bit, stereo
            while True:
                await response.write(silence)
                await asyncio.sleep(0.01) 
        except (ConnectionResetError, ConnectionError, BrokenPipeError, asyncio.CancelledError):
            pass
        return response

    async def start(self):
        app = web.Application()
        app.router.add_get('/sendspin_dummy', self.handle_dummy_audio)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', self.port)
        await site.start()

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

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
        self.kodi_ui = KodiMetadataHandler()
        self.dummy_server = DummyStreamServer()
        self.client: SendspinClient = None
        self.sample_rate_max = 48000
        self.channels = 2
        self.bit_depth = 16
        self.buffer_bytes = int((BUFFERSIZE_REQUEST_MS / 1000.0) * self.sample_rate_max * self.channels * (self.bit_depth // 8))
        self.is_playing = False
        self.player_state = PlaybackStateType.STOPPED
    
    async def setup(self):
        """Registers listeners and starts the Client Listener."""

        #define capabilities
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
            self.logger.info(f"Connected to Sendspin server. Name: {info.name} ID: { info.server_id }")
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
        await self.dummy_server.start()
        await self.setup()
        monitor = xbmc.Monitor()
        while not monitor.abortRequested():
            await asyncio.sleep(1)
        await self.cleanup()

    async def cleanup(self):
        """Clean shutdown."""
        self.engine.stop()
        self.router.cleanup()
        await self.client.disconnect()
        self.logger.info("Shutting down Sendspin service...")

    def on_stream_start(self, message: StreamStartMessage):
        """Triggered when Sendspin starts a stream."""
        self.logger.info(f"Stream Start Received")
        self.logger.info(f"Sample Rate: {message.payload.player.sample_rate}, Channels: {message.payload.player.channels}, Bit Depth: {message.payload.player.bit_depth}")
        self.is_playing = True

        virtual_sink = self.router.setup_routing()
        self.engine.start(
            rate = message.payload.player.sample_rate,
            channels = message.payload.player.channels,
            bit_depth = message.payload.player.bit_depth,
            target_sink = virtual_sink
        )
        self.player_state = PlaybackStateType.PLAYING
        self.engine.set_volume(50)
        asyncio.create_task(self.client.send_player_state(state=PlayerStateType.SYNCHRONIZED, volume=50, muted=False))

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
            self.kodi_ui.update_metadata(title="Sendspin Stream", artist="Sendspin Stream")

    def on_stream_end(self, roles=None):
        """Triggered when stream ends."""
        self.logger.info("Stream End received")
        self.is_playing = False
        self.player_state = PlaybackStateType.STOPPED
        asyncio.create_task(self._async_stop_sequence())
    
    async def _async_stop_sequence(self):
        self.kodi_ui.stop()
        await asyncio.sleep(4)
        self.engine.stop()

    def on_server_command(self, payload: ServerCommandPayload):
        """Handle Volume/Mute commands from the Sendspin server."""
        command_data = getattr(payload, 'player', None)
        self.logger.debug(f"Server Command received: {command_data.command}")

        try:
            # Command is 'player.volume', payload has 'player.volume' integer (0-100)
            if command_data.command == PlayerCommand.VOLUME:
                # getattr is safe if the attribute is missing, defaulting to None
                vol = getattr(command_data, 'volume', None)
                if vol is not None:
                    self.engine.set_volume(vol)
                else:
                    self.logger.warning("Received VOLUME command but payload.volume is None")
            else:
                self.logger.debug(f"Ignored unhandled command: {command_data.command}")

        except Exception as e:
            self.logger.error(f"Error handling server command: {e}")

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
