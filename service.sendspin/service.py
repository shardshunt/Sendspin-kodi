#!/usr/bin/env python3

# Ensure vendored libs in resources/lib are importable
import os, sys, traceback
ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR_LIB = os.path.join(ADDON_ROOT, "resources", "lib")
if os.path.isdir(VENDOR_LIB) and VENDOR_LIB not in sys.path:
    sys.path.insert(0, VENDOR_LIB)

import struct
import asyncio
import logging
import xbmc
import xbmcgui
import xbmcaddon

# --- Settings ---

addon = xbmcaddon.Addon()
def _get_setting_str(key: str, default: str) -> str:
    try:
        v = addon.getSetting(key)
        return v if v else default
    except Exception:
        return default

def _get_setting_int(key: str, default: int) -> int:
    try:
        v = addon.getSetting(key)
        return int(v) if v else default
    except Exception:
        return default

# --- Log paths ---
LOG_PATH = _get_setting_str("log_path", "/storage/.kodi/temp/sendspin-service.log")
STARTUP_ERR_PATH = _get_setting_str("startup_err_path", "/storage/.kodi/temp/sendspin-startup-error.txt")

# --- Safe startup guard ---
try:
    pass
except Exception:
    with open(STARTUP_ERR_PATH, "w") as f:
        f.write("Startup import error\n")
        traceback.print_exc(file=f)
    raise

# --- Logging setup ---
def setup_logging():
    logger = logging.getLogger("sendspin")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(LOG_PATH)
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    class KodiHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                level = xbmc.LOGDEBUG
                if record.levelno >= logging.ERROR:
                    level = xbmc.LOGERROR
                elif record.levelno >= logging.WARNING:
                    level = xbmc.LOGWARNING
                elif record.levelno >= logging.INFO:
                    level = xbmc.LOGINFO
                xbmc.log(f"[Sendspin] {msg}", level)
            except Exception:
                pass

    kh = KodiHandler()
    kh.setLevel(logging.DEBUG)
    kh.setFormatter(fmt)
    logger.addHandler(kh)

    logger.propagate = False
    return logger

logger = setup_logging()

def log_info(msg): logger.info(msg)
def log_debug(msg): logger.debug(msg)
def log_exception(msg): logger.exception(msg)

# --- Config (from settings with defaults) ---
PROXY_PORT = _get_setting_int("proxy_port", 59999)
SERVER_URL = _get_setting_str("server_url", "ws://192.168.0.161:8927/sendspin")
CLIENT_ID = _get_setting_str("client_id", "kodi-sendspin-client")
CLIENT_NAME = _get_setting_str("client_name", "Kodi Room")

# --- WAV header ---
def create_wav_header(sample_rate=48000, channels=2, bits=16):
    byte_rate = sample_rate * channels * (bits // 8)
    block_align = channels * (bits // 8)
    return (
        b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + b"fmt " +
        struct.pack("<I", 16) + struct.pack("<H", 1) +
        struct.pack("<H", channels) + struct.pack("<I", sample_rate) +
        struct.pack("<I", byte_rate) + struct.pack("<H", block_align) +
        struct.pack("<H", bits) + b"data" + b"\xff\xff\xff\xff"
    )

# --- Audio Proxy ---
class AudioProxy:
    def __init__(self):
        self.log = logging.getLogger("sendspin.proxy")
        self.audio_queue = asyncio.Queue(maxsize=200)
        self.client = None
        self.chunks_received = 0
        self.chunks_sent = 0
        self._player = xbmc.Player()
        self._playing = False
        self.current_format = None
        self._http_stream_active = False

    # --- Kodi playback control ---
    def _start_kodi_playback(self):
        if self._playing:
            self.log.info("Kodi playback already active")
            return

        stream_url = f"http://127.0.0.1:{PROXY_PORT}/stream.wav"
        self.log.info(f"Starting Kodi playback: {stream_url}")

        li = xbmcgui.ListItem(label="Sendspin Stream")
        li.setInfo("music", {"title": "Sendspin Stream"})
        li.setPath(stream_url)
        li.setMimeType("audio/wav")
        li.setProperty("IsPlayable", "true")
        li.setContentLookup(False)

        try:
            self._player.play(stream_url, li, False)
            self._playing = True
            self.log.info("Kodi player.play() invoked")
        except Exception:
            log_exception("Failed to start Kodi playback")

    def _stop_kodi_playback(self):
        if not self._playing:
            self.log.info("Kodi playback not active")
            return
        try:
            self.log.info("Stopping Kodi playback")
            self._player.stop()
        except Exception:
            log_exception("Failed to stop Kodi playback")
        finally:
            self._playing = False

    # --- Sendspin client startup ---
    async def start_client(self):
        try:
            from aiosendspin.client import SendspinClient
            from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
            from aiosendspin.models.types import Roles, AudioCodec, PlayerCommand
        except Exception as e:
            log_exception(f"Failed to import aiosendspin: {e}")
            raise

        try:
            player_support = ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(codec=AudioCodec.PCM, channels=2, sample_rate=48000, bit_depth=16)
                ],
                buffer_capacity=48000 * 2 * 2,
                supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE]
            )

            roles = [Roles.PLAYER, Roles.CONTROLLER, Roles.METADATA]

            self.client = SendspinClient(
                CLIENT_ID,
                CLIENT_NAME,
                roles,
                player_support=player_support
            )

            self.client.set_audio_chunk_listener(self.on_audio_chunk)
            self.client.set_stream_start_listener(self.on_stream_start)
            self.client.set_stream_end_listener(self.on_stream_end)
            self.client.set_metadata_listener(self.on_metadata)
            self.client.set_controller_state_listener(self.on_controller_state)

            self.log.info(f"Connecting to {SERVER_URL}")
            await self.client.connect(SERVER_URL)
            self.log.info("Connected to Sendspin server")

        except Exception:
            log_exception("Sendspin client connection failed")
            raise

    # --- Callbacks ---
    async def on_audio_chunk(self, timestamp, data, fmt):
        self.chunks_received += 1
        # Record the format reported by the server/client stack so we can
        # generate a correct WAV header for HTTP clients.
        try:
            if fmt is not None and self.current_format is None:
                # fmt is a PCMFormat-like object with sample_rate, channels, bit_depth
                self.current_format = fmt
                self.log.info(f"Audio format detected: {fmt}")
        except Exception:
            pass
        if self.audio_queue.full():
            try: self.audio_queue.get_nowait()
            except: pass
        await self.audio_queue.put(data)
        if self.chunks_received % 100 == 0:
            log_debug(f"Chunks received: {self.chunks_received}")

    async def on_stream_start(self, message):
        self.log.info("Stream start received")
        # Try to capture the format from the stream start payload so that
        # HTTP clients can receive a correct WAV header immediately.
        try:
            player = getattr(message, "payload", None) and getattr(message.payload, "player", None)
            if player is not None:
                self.current_format = player
                self.log.info(f"Audio format from stream start: {player}")
        except Exception:
            pass

        self._start_kodi_playback()

    async def on_stream_end(self, roles):
        self.log.info("Stream end received")
        self._stop_kodi_playback()

    async def on_metadata(self, payload):
        self.log.info(f"Metadata update: {payload.metadata}")

    async def on_controller_state(self, payload):
        self.log.info(f"Controller state update: {payload}")

    # --- HTTP streaming endpoint ---
    async def stream_handler(self, request):
        self.log.info(f"Player connected {request.remote}")
        from aiohttp import web
        from aiohttp import client_exceptions

        # Refuse additional concurrent HTTP stream connections to avoid
        # multiple Kodi probes fighting over the same stream.
        if self._http_stream_active:
            self.log.info("Another HTTP client attempted to connect while stream active; refusing")
            return web.Response(status=503, reason="Stream busy")

        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "audio/wav", "Cache-Control": "no-cache"}
        )
        await resp.prepare(request)

        # Wait briefly for the first audio format to be reported so we can
        # emit a matching WAV header. If no format arrives within the
        # timeout, fall back to defaults (48000 Hz, stereo, 16-bit).
        header = None
        try:
            timeout_s = 2.0
            poll_interval = 0.05
            waited = 0.0
            while self.current_format is None and waited < timeout_s:
                await asyncio.sleep(poll_interval)
                waited += poll_interval

            if self.current_format is not None:
                fmt = self.current_format
                # Some format objects use 'bit_depth' attribute name
                bits = getattr(fmt, "bit_depth", getattr(fmt, "bits", 16))
                header = create_wav_header(sample_rate=getattr(fmt, "sample_rate", 48000),
                                           channels=getattr(fmt, "channels", 2),
                                           bits=bits)
            else:
                self.log.info("No audio format reported before HTTP client connected; using defaults")
                header = create_wav_header()
        except Exception:
            log_exception("Failed to determine audio format for WAV header")
            header = create_wav_header()

        # mark active
        self._http_stream_active = True
        try:
            await resp.write(header)

            while True:
                data = await self.audio_queue.get()
                try:
                    await resp.write(data)
                except client_exceptions.ClientConnectionResetError:
                    # Client closed the connection unexpectedly; stop the handler
                    self.log.info("HTTP client disconnected (connection reset)")
                    # Do not re-raise; break to clean up
                    break
                except Exception:
                    # Other write errors — log and stop streaming
                    log_exception("Stream write error")
                    break

                self.chunks_sent += 1
                try:
                    self.audio_queue.task_done()
                except Exception:
                    pass

        except asyncio.CancelledError:
            self.log.info("Stream cancelled")
        finally:
            # Attempt to close the response cleanly; ignore failures
            try:
                await resp.write_eof()
            except Exception:
                pass
            self._http_stream_active = False
        return resp

# --- Main service ---
async def run_service():
    log_info("Starting Sendspin service")
    proxy = AudioProxy()

    from aiohttp import web
    app = web.Application()
    app.router.add_get("/stream.wav", proxy.stream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PROXY_PORT)
    await site.start()

    log_info(f"Proxy listening at http://127.0.0.1:{PROXY_PORT}/stream.wav")

    asyncio.create_task(proxy.start_client())

    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        await asyncio.sleep(1)

    log_info("Shutting down Sendspin service")
    if proxy.client:
        await proxy.client.disconnect()
    await runner.cleanup()

# --- Entrypoint ---
def main():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_service())
    except Exception:
        with open(STARTUP_ERR_PATH, "w") as f:
            f.write("Exception during service startup\n")
            traceback.print_exc(file=f)
        log_exception("Unhandled exception in service main")
        raise

if __name__ == "__main__":
    main()
