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
import asyncio, logging, subprocess
import xbmc, xbmcaddon

# aiosendspin imports
from aiosendspin.client import SendspinClient, PCMFormat, SendspinTimeFilter, ClientListener
from aiosendspin.models.types import Roles, AudioCodec, PlayerCommand
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


class CLIPlaybackEngine:
    """
    Handles audio playback by piping raw PCM data into the 'pacat' command.
    """
    def __init__(self):
        self.process = None
        self.logger = logging.getLogger("sendspin")

    def start(self, rate, channels, bit_depth, target_sink):
        """Starts the pacat process targeting the specific sink."""
        self.stop()
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
        try:
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            self.logger.info(f"started player with command{' '.join(cmd)}")
        except Exception as e:
            self.logger.info(f"exception staring aplay with: {' '.join(cmd)}. Exception: {e}")

    def play_chunk(self, data):
        """Writes a chunk of PCM data to the player process."""
        if not self.process:
            return

        # Check if pcat died silently
        poll = self.process.poll()
        if poll is not None:
            err = self.process.stderr.read().decode().strip()
            self.logger.info(f"[Sendspin] pacat died with code {poll}. Error: {err}")
            self.stop()
            return

        try:
            self.process.stdin.write(data)
        except BrokenPipeError:
            err = self.process.stderr.read().decode().strip()
            self.logger.info(f"[Sendspin] pacat pipe broken: {err}")
            self.stop()

    def stop(self):
        """Terminates the playback process."""
        if self.process:
            self.logger.info("stopping playback engine")
            self.process.terminate()
            self.process = None


class SendspinServiceController:
    """
    Main Service Controller.
    
    Orchestrates the Sendspin client, the audio router, and the playback engine.
    Handles network events and routes audio data to the player.
    """
    def __init__(self):
        self.logger = logging.getLogger("sendspin")
        self.addon = xbmcaddon.Addon()
        self.engine = CLIPlaybackEngine()
        self.router = AudioRouter()
        self.client: SendspinClient = None
        self.sample_rate = 48000
        self.channels = 2
        self.bit_depth = 16
        self.buffer_bytes = int((BUFFERSIZE_REQUEST_MS / 1000.0) * self.sample_rate * self.channels * (self.bit_depth // 8))
    
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
            static_delay_ms=100,
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

        virtual_sink = self.router.setup_routing()

        self.engine.start(
            rate = message.payload.player.sample_rate,
            channels = message.payload.player.channels,
            bit_depth = message.payload.player.bit_depth,
            target_sink = virtual_sink
        )

    def on_audio_chunk(self, server_timestamp_us: int, audio_data: bytes, audio_format):
        """Handles incoming audio data chunks."""
        self.engine.play_chunk(audio_data)


    def on_stream_end(self, roles=None):
        """Triggered when stream ends."""
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
