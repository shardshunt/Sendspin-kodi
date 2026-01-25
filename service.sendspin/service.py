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
from aiohttp import web

# aiosendspin imports
from aiosendspin.client import SendspinClient, PCMFormat, SendspinTimeFilter, ClientListener
from aiosendspin.models.types import Roles, AudioCodec, PlayerCommand, UndefinedField
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.core import StreamStartMessage, ServerCommandPayload

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
BUFFERSIZE_REQUEST_MS = 5000 # 5 seconds

class AudioRouter:
    """
    Manages PulseAudio modules to ensure audio routing works correctly.
    
    It creates a virtual Null Sink ('Sendspin_Sink') and loops it back 
    to the physical hardware. This allows Sendspin to play audio even 
    if Kodi holds a lock on the hardware device, as PulseAudio handles 
    the mixing.
    """
    def __init__(self):
        self.sink_name = "Sendspin_Sink"
        self.loopback_id = None
        self.null_sink_id = None
        self.logger = logging.getLogger("sendspin")

    def _run_pactl(self, args):
        try:
            result = subprocess.run(['pactl'] + args, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            self.logger.info(f"[Sendspin] pactl error: {e}",)
        return None

    def setup_routing(self):
        """Creates a virtual sink and loops it to hardware."""
        if not self.null_sink_id:
            cmd = ['load-module', 'module-null-sink', f'sink_name={self.sink_name}', 'sink_properties=device.description=Sendspin_Virtual_Cable']
            self.null_sink_id = self._run_pactl(cmd)


        hw_sink = self.get_hardware_sink()

        if hw_sink and not self.loopback_id:
            loop_cmd = [
                'load-module', 'module-loopback',
                f'source={self.sink_name}.monitor',
                f'sink={hw_sink}',
                'latency_msec=100'
            ]
            self.loopback_id = self._run_pactl(loop_cmd)
            self.logger.info(f"Routing {self.sink_name} -> {hw_sink}")
            
        return self.sink_name

    def get_hardware_sink(self):
        """Finds the actual alsa_output sink, ignoring virtual devices."""
        out = self._run_pactl(['list', 'sinks', 'short'])
        if out:
            for line in out.split('\n'):
                if "alsa_output" in line:
                    return line.split('\t')[1]
        return "@DEFAULT_SINK@"

    def cleanup(self):
        """Clean up modules to prevent PulseAudio from getting cluttered."""
        if self.loopback_id:
            self._run_pactl(['unload-module', self.loopback_id])
            self.loopback_id = None
        if self.null_sink_id:
            self._run_pactl(['unload-module', self.null_sink_id])
            self.null_sink_id = None


class SyncPlaybackEngine:
    """
    Handles audio playback by piping raw PCM data into the 'pacat' command.
    """
    def __init__(self):
        self.process = None
        self.logger = logging.getLogger("sendspin")
        self._queue = asyncio.PriorityQueue()
        self._worker_task = None
        self._running = False
        self.target_latency = 0.2 # Seconds

    def set_time_provider(self, time_provider_func, sync_check_func):
        """Link the engine to the Client's time filter."""
        self.get_play_time = time_provider_func
        self.is_synchronized = sync_check_func

    def start(self, rate, channels, bit_depth, target_sink):
        """Starts the pacat process targeting the specific sink."""
        fmt = f"s{bit_depth}le"
        cmd = [
            'pacat', 
            '--playback',
            '--device', target_sink,
            '--format', fmt, 
            '--rate', str(rate), 
            '--channels', str(channels),
            '--latency-msec=100',
            '--client-name=SendspinPlayer'
        ]

        self.logger.info(f"Starting Scheduled pacat: {' '.join(cmd)}")
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self._running = True
        self._worker_task = asyncio.create_task(self._scheduler_loop())


    def play_chunk(self, server_timestamp_us, data):
        """Writes a chunk of PCM data to the player process."""
        if not self.is_synchronized:
            return
        
        local_target_us = self.get_play_time(server_timestamp_us)
        scheduled_time = (local_target_us / 1_000_000.0) + self.target_latency
        self._queue.put_nowait((scheduled_time, data))
    
    async def _scheduler_loop(self):
        """The 'Metronome' loop that releases audio to the pipe."""
        while self._running:
            try:
                # Peek at the next chunk
                scheduled_time, data = await self._queue.get()
                
                # How long until this chunk is due?
                # loop.time() is the reference for the TimeFilter's T_client
                now = asyncio.get_event_loop().time()
                wait_time = scheduled_time - now
                
                if wait_time > 0:
                    # Sleep until the exact micro-moment it's due
                    await asyncio.sleep(wait_time)
                
                # Release to the PulseAudio pipe
                if self.process and self.process.stdin:
                    try:
                        self.process.stdin.write(data)
                        self.process.stdin.flush()
                    except (BrokenPipeError, AttributeError):
                        self.logger.error("pacat pipe broken.")
                        self.process = None
                        break
                
                self._queue.task_done()
            except Exception as e:
                self.logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(0.01)

    def stop(self):
        """Terminates the playback process."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
        if self.process:
            self.process.terminate()
            self.process = None
        # Flush queue
        while not self._queue.empty():
            self._queue.get_nowait()

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
        if album:
            info_tag.setAlbum(album)
        
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
        
        self.logger.info("Starting dummy playback for UI")
        self.player.play("http://localhost:9999/sendspin_dummy", list_item)
        self.player.updateInfoTag(list_item)

    def stop(self):
        """Stops the Kodi UI player."""
        if self.player.isPlaying():
            self.player.stop()

class DummyStreamServer:
    def __init__(self, port=9999):
        self.port = port
        self.runner = None
        self.logger = logging.getLogger("sendspin")

    def _create_wav_header(self, sample_rate=11025, channels=1, bits=16):
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
            silence = b'\x00' * (11025 * 2 * 2)
            while True:
                await response.write(silence)
                await asyncio.sleep(0.02) 
        except (ConnectionResetError, asyncio.CancelledError):
            self.logger
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
    
    async def setup(self):
        """Registers listeners and starts the Client Listener."""

        #define capabilities
        self.player_support = ClientHelloPlayerSupport(
            supported_formats = [ 
                SupportedAudioFormat(
                    AudioCodec.PCM,
                    channels=2,
                    sample_rate=48000,
                    bit_depth=16,
                ),
                SupportedAudioFormat(
                    AudioCodec.PCM,
                    channels=2,
                    sample_rate=44100,
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
            roles=[Roles.PLAYER, Roles.METADATA],
            player_support=self.player_support,
            static_delay_ms=0,
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

    def on_audio_chunk(self, server_timestamp_us: int, audio_data: bytes, audio_format):
        """Handles incoming audio data chunks."""
        self.engine.play_chunk(server_timestamp_us, audio_data)
    
    def on_metadata_update(self, payload):
        """Called when track info (Artist/Title/Art) changes."""
        metadata = getattr(payload, 'metadata', {})

        title = getattr(metadata, 'title', 'Unknown')
        artist = getattr(metadata, 'artist', 'Unknown')
        thumb = getattr(metadata, 'artwork_url', '')
        
        self.logger.info(f"Metadata Update: {artist} - {title}")
        if isinstance(title, str) and isinstance(artist, str) and self.is_playing:
            self.kodi_ui.update_metadata(title=title, artist=artist, thumb=thumb)


    def on_stream_end(self, roles=None):
        """Triggered when stream ends."""
        self.is_playing = False
        self.kodi_ui.stop()
        self.engine.stop()
        self.logger.info("Stream End received")

    def on_server_command(self, payload: ServerCommandPayload):
        """Handle Volume/Mute commands."""
        self.logger.debug("Server Command received")

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
