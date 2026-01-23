#!/usr/bin/env python3
"""
Sendspin Service for Kodi.

This service runs in the background, connects to a Sendspin server,
buffers received audio, and streams it locally to the Kodi player via an HTTP proxy.
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
import asyncio
import logging
import xbmc, xbmcgui, xbmcaddon
from struct import pack

# third-party imports
import aiohttp
from aiohttp import web

# aiosendspin imports
from aiosendspin.client import SendspinClient, PCMFormat, SendspinTimeFilter, ClientListener
from aiosendspin.models.types import Roles, AudioCodec, PlayerCommand
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.core import StreamStartMessage, ServerCommandPayload

# local imports
import logger

class ThrottledLogger:
    def __init__(self, interval=0.5):
        self.interval = interval
        self.last_log_time = 0

    def log(self, message):
        current_time = time.time()
        if current_time - self.last_log_time >= self.interval:
            xbmc.log(f"[Sendspin-Debug] {message}", level=xbmc.LOGDEBUG)
            self.last_log_time = current_time

# Initialize the helper
throttledLog = ThrottledLogger(1)

# --- CONFIGURATION & UTILITIES ---

PROXY_PORT = xbmcaddon.Addon().getSetting("proxy_port") or 59999
# SERVER_URL = xbmcaddon.Addon().getSetting("server_url") or "ws://192.168.0.161:8927/sendspin"
CLIENT_ID = xbmcaddon.Addon().getSetting("client_id") or "kodi-sendspin-client"
CLIENT_NAME = "Kodi" #xbmcaddon.Addon().getAddonInfo("client_name_") or "Kodi"
PROXY_HOST = "127.0.0.1"
BUFFERSIZE_REQUEST_MS = 5000 # 5 seconds

class AudioStreamBuffer:
    def __init__(self):
        self.logger = logging.getLogger("sendspin")
        self._queue = asyncio.Queue(maxsize=500) 
        self._format = None
        self._format_event = asyncio.Event()

    def set_format(self, pcm_format):
        self._format = pcm_format
        self._format_event.set()

    def clear(self):
        """Clears the buffer and resets sync state."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._format_event.clear()

    def _get_wav_header(self) -> bytes:
        if not self._format: return b""
        bps = self._format.bit_depth // 8
        rate = self._format.sample_rate
        channels = self._format.channels
        # Use a large positive value for chunk sizes to indicate a very long stream.
        # 0x7FFFFFF0 is a large positive 32-bit value, chosen to be a bit less than max.
        # Some players may misinterpret 0xFFFFFFFF (-1 as signed int).
        large_size = 0x7FFFFFF0
        riff_chunk_size = large_size + 36 # 36 bytes for the header fields before data
        return (b"RIFF" + pack("<I", riff_chunk_size) + b"WAVE" + b"fmt " +
                pack("<I", 16) + pack("<H", 1) +
                pack("<H", channels) + pack("<I", rate) +
                pack("<I", rate * channels * bps) + 
                pack("<H", channels * bps) +
                pack("<H", self._format.bit_depth) + b"data" + pack("<I", large_size))

    def put_audio(self, server_timestamp_us: int, data: bytes, pcm_format):
        """Puts audio data into the buffer."""
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(data)


    async def stream_generator(self, _):
        """ Yields audio chunks for the HTTP response. """
        await self._format_event.wait()
        yield self._get_wav_header()
        while True:
            chunk = await self._queue.get()
            yield chunk


class LocalAudioProxy:
    """
    A lightweight HTTP Server running inside Kodi.
    """
    def __init__(self, buffer: AudioStreamBuffer, port: int = PROXY_PORT):
        self._buffer = buffer
        self._port = port
        self._client: SendspinClient = None
        self._runner: web.AppRunner = None
        self.logger = logging.getLogger("sendspin")
    
    async def start(self, client: SendspinClient):
        self._client = client
        app = web.Application()
        app.router.add_get('/stream.wav', self.handle_stream)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, PROXY_HOST, self._port)
        await site.start()

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    async def handle_stream(self, request):
        """Handles the HTTP GET request from Kodi Player."""
        response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'audio/x-wav',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Accept-Ranges': 'none',
            }
        )
        await response.prepare(request)

        try:
            # Consume the buffer generator
            async for chunk in self._buffer.stream_generator(self._client):
                await response.write(chunk)
        except (aiohttp.client_exceptions.ClientConnectionResetError, asyncio.CancelledError):
            self.logger.info("Client disconnected from the stream.")
        
        return response

    def get_stream_url(self) -> str:
        """Returns the local URL that Kodi should play."""
        return f"http://{PROXY_HOST}:{self._port}/stream.wav"
    
class KodiPlayerManager(xbmc.Player):
    """
    Wrapper around xbmc.Player to handle playback triggers.
    """
    
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
    
    def play_url(self, url: str):
        """Instructs Kodi to open the HTTP Proxy URL."""
        item = xbmcgui.ListItem("Sendspin Stream")
        item.setProperty("MimeType", "audio/wav")
        item.setProperty("TotalTime", "0")
        item.setProperty("IsLive", 'true')
        music_tag = item.getMusicInfoTag()
        music_tag.setTitle("Sendspin Stream")
        music_tag.setArtist("Sendspin")
        self.play(url, item)

    def onPlayBackStopped(self):
        """Override: Called when Kodi stops playing."""
        self.controller.on_kodi_player_stopped()

class SendspinServiceController:
    """
    Main Service Controller.
    """
    def __init__(self):
        self.logger = logging.getLogger("sendspin")
        self.addon = xbmcaddon.Addon()
        self.buffer = AudioStreamBuffer()
        self.client: SendspinClient = None
        self.proxy = LocalAudioProxy(self.buffer)
        self.player = KodiPlayerManager(self)
        bytes_per_sec_max = 48000 * 2 * 2  # 48kHz, 16-bit, stereo
        self.buffer_bytes = int((BUFFERSIZE_REQUEST_MS / 1000.0) * bytes_per_sec_max)
        self.is_playing = False
    
    async def setup(self):
        """Registers listeners and starts the Proxy."""

        #define capabilities
        self.player_support = ClientHelloPlayerSupport(
            supported_formats = [ 
                SupportedAudioFormat(
                    AudioCodec.PCM,
                    channels=2,
                    sample_rate=48000,
                    bit_depth=16,
                )
            ],
            buffer_capacity = self.buffer_bytes,
            supported_commands= [ PlayerCommand.VOLUME, PlayerCommand.MUTE ],
        )

        #initialize client
        self.client = SendspinClient(
            client_id=CLIENT_ID,
            client_name=CLIENT_NAME,
            roles=[Roles.PLAYER],
            player_support=self.player_support,
            static_delay_ms=3000,
        )

        handlers = {
            "add_stream_start_listener": self.on_stream_start,
            "add_stream_end_listener": self.on_stream_end,
            "add_server_command_listener": self.on_server_command,
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
        await self.setup()
        await self.proxy.start(self.client)

        monitor = xbmc.Monitor()
        while not monitor.abortRequested():
                await asyncio.sleep(1)

    async def cleanup(self):
        """Clean shutdown."""
        self.logger.info("Shutting down Sendspin service...")
        await self.client.disconnect()
        await self.proxy.stop()

    def on_stream_start(self, message: StreamStartMessage):
        """Triggered when Sendspin starts a stream."""
        self.logger.info("Stream Start received")
        if message.payload.player:
            self.logger.info(f"Stream Format: {message.payload.player.sample_rate} Hz, {message.payload.player.channels} channels, {message.payload.player.bit_depth} bit")
            fmt = PCMFormat(
                sample_rate=message.payload.player.sample_rate,
                channels=message.payload.player.channels,
                bit_depth=message.payload.player.bit_depth
            )
            self.buffer.set_format(fmt)
        self.is_playing = False

    def on_audio_chunk(self, server_timestamp_us: int, audio_data: bytes, audio_format):
            self.buffer.put_audio(server_timestamp_us, audio_data, audio_format.pcm_format)

            if not self.is_playing and self.buffer._queue.qsize() >= 50:
                self.is_playing = True
                self.logger.info(f"Buffer primed, queue size {self.buffer._queue.qsize()}. Triggering Kodi player...")
                stream_url = self.proxy.get_stream_url()
                self.player.play_url(self.proxy.get_stream_url())
                self.logger.info(f"Kodi player started with URL: {stream_url}")

    def on_stream_end(self, roles=None):
        """Triggered when stream ends."""
        self.logger.info("Stream End received")
        self.player.stop()
        self.buffer.clear()
        self.is_playing = False

    def on_server_command(self, payload: ServerCommandPayload):
        """Handle Volume/Mute commands."""
        self.logger.debug("Server Command received")
    
    def on_kodi_player_stopped(self):
        """Helper called by KodiPlayerManager when playback ends/stops."""
        self.is_playing = False

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
