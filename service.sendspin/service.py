#!/usr/bin/env python3

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
from aiosendspin.models.types import PlayerCommand

# --- Settings ---
addon = xbmcaddon.Addon()
def _get_setting_str(key: str, default: str) -> str:
    try:
        v = addon.getSetting(key)
        return v if v else default
    except Exception: return default

def _get_setting_int(key: str, default: int) -> int:
    try:
        v = addon.getSetting(key)
        return int(v) if v else default
    except Exception: return default

LOG_PATH = _get_setting_str("log_path", "/storage/.kodi/temp/sendspin-service.log")

def setup_logging():
    logger = logging.getLogger("sendspin")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    class KodiHandler(logging.Handler):
        def emit(self, record):
            try:
                xbmc.log(f"[Sendspin] {self.format(record)}", xbmc.LOGINFO if record.levelno < 40 else xbmc.LOGERROR)
            except Exception: pass
    kh = KodiHandler(); kh.setFormatter(fmt); logger.addHandler(kh)
    logger.propagate = False
    return logger

logger = setup_logging()

# --- Config ---
PROXY_PORT = _get_setting_int("proxy_port", 59999)
SERVER_URL = _get_setting_str("server_url", "ws://192.168.0.161:8927/sendspin")
CLIENT_ID = _get_setting_str("client_id", "kodi-sendspin-client")
CLIENT_NAME = _get_setting_str("client_name", "Kodi Room")

def create_wav_header(sample_rate=48000, channels=2, bits=16):
    byte_rate = sample_rate * channels * (bits // 8)
    block_align = channels * (bits // 8)
    return (b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + b"fmt " +
            struct.pack("<I", 16) + struct.pack("<H", 1) +
            struct.pack("<H", channels) + struct.pack("<I", sample_rate) +
            struct.pack("<I", byte_rate) + struct.pack("<H", block_align) +
            struct.pack("<H", bits) + b"data" + b"\xff\xff\xff\xff")

class AudioProxy:
    def __init__(self):
        self.log = logging.getLogger("sendspin.proxy")
        self.raw_log = logging.getLogger("sendspin.raw")
        self.audio_queue = asyncio.Queue(maxsize=200)
        self.client = None
        self._player = xbmc.Player()
        self._playing = False
        self._is_restarting = False
        self.current_metadata = {}
        self.current_artwork_url = None
        self._stop_event = asyncio.Event()

    def _log_metadata_summary(self, context="Playback"):
        title = self.current_metadata.get('title', 'Unknown Title')
        artist = self.current_metadata.get('artist', 'Unknown Artist')
        album = self.current_metadata.get('album', 'Unknown Album')
        duration = self.current_metadata.get('duration', 0)
        art = "Yes" if self.current_artwork_url else "No"
        self.log.info(f"[{context}] Track: {artist} - {title} | Album: {album} | Duration: {duration}s | Has Art: {art}")

    def _start_kodi_playback(self):
        stream_url = f"http://127.0.0.1:{PROXY_PORT}/stream.wav"
        self._log_metadata_summary("Starting Kodi Music Playback")

        li = xbmcgui.ListItem(label=self.current_metadata.get('title', 'Sendspin'), offscreen=True)
        li.setPath(stream_url)
        li.setProperty('IsPlayable', 'true')
        
        t = li.getMusicInfoTag()
        t.setTitle(self.current_metadata.get('title', 'Sendspin Stream'))
        t.setArtist(self.current_metadata.get('artist', 'Sendspin'))
        t.setAlbum(self.current_metadata.get('album', ''))
        
        # Add duration (Sendspin ms -> Kodi seconds)
        duration_ms = self.current_metadata.get('duration_ms', 0)
        if duration_ms > 0:
            t.setDuration(int(duration_ms / 1000))
        
        t.setMediaType('music')
        
        if self.current_artwork_url:
            li.setArt({
                'thumb': self.current_artwork_url,
                'icon': self.current_artwork_url,
                'poster': self.current_artwork_url,
                'clearart': self.current_artwork_url
            })

        try:
            self._stop_event.clear()
            self._player.play(item=stream_url, listitem=li, windowed=False)
            self._playing = True
        except Exception:
            logger.exception("Failed to start Kodi playback")

    def _stop_kodi_playback(self):
        if not self._playing:
            return
        try:
            self.log.info("Stopping Kodi playback...")
            self._stop_event.set()
            while not self.audio_queue.empty():
                try: self.audio_queue.get_nowait()
                except: break
            self._player.stop()
        except Exception:
            logger.exception("Failed to stop Kodi playback")
        finally:
            self._playing = False

    async def start_client(self):
        try:
            from aiosendspin.client import SendspinClient
            from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
            from aiosendspin.models.types import Roles, AudioCodec
            
            ps = ClientHelloPlayerSupport(
                supported_formats=[SupportedAudioFormat(codec=AudioCodec.PCM, channels=2, sample_rate=48000, bit_depth=16)],
                buffer_capacity=48000 * 2 * 2,
                supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE]
            )

            self.client = SendspinClient(CLIENT_ID, CLIENT_NAME, [Roles.PLAYER, Roles.CONTROLLER, Roles.METADATA], player_support=ps)

            # --- Monkey Patch for Raw Logging (filtering server/time) ---
            original_handle_json = self.client._handle_json_message
            async def patched_handle_json(data: str):
                if '"type":"server/time"' not in data:
                    self.raw_log.debug(f"RAW RECV: {data}")
                await original_handle_json(data)
            self.client._handle_json_message = patched_handle_json

            self.client.set_audio_chunk_listener(self.on_audio_chunk)
            self.client.set_stream_start_listener(self.on_stream_start)
            self.client.set_stream_end_listener(self.on_stream_end)
            self.client.set_metadata_listener(self.on_metadata)
            self.client.set_controller_state_listener(self.on_controller_state)

            await self.client.connect(SERVER_URL)
            while self.client and self.client.connected:
                await asyncio.sleep(1)
        except Exception:
            logger.exception("Sendspin client failed")

    async def on_audio_chunk(self, ts, data, fmt):
        if self.audio_queue.full():
            try: self.audio_queue.get_nowait()
            except: pass
        await self.audio_queue.put(data)

    async def on_stream_start(self, msg):
        self.log.info("Stream start signal received from server")
        self._start_kodi_playback()

    async def on_stream_end(self, roles):
        self.log.info("Stream end signal received from server")
        await self.audio_queue.put(None)
        self._stop_kodi_playback()

    async def on_metadata(self, payload):
        if not payload or not hasattr(payload, "metadata"):
            return

        meta = payload.metadata
        def is_def(v): return v is not None and type(v).__name__ != 'UndefinedField'

        changed = False
        # Basic tags
        for k in ['title', 'artist', 'album']:
            val = getattr(meta, k, None)
            if is_def(val) and self.current_metadata.get(k) != val:
                self.current_metadata[k] = val
                changed = True

        # Extract Duration from progress object
        if hasattr(meta, 'progress') and is_def(meta.progress):
            dur = getattr(meta.progress, 'track_duration', 0)
            if dur != self.current_metadata.get('duration_ms'):
                self.current_metadata['duration_ms'] = dur
                self.current_metadata['duration'] = int(dur / 1000)
                changed = True

        if hasattr(meta, 'artwork_url') and is_def(meta.artwork_url):
            if self.current_artwork_url != meta.artwork_url:
                self.current_artwork_url = meta.artwork_url
                changed = True

        if changed:
            if self._playing and not self._is_restarting:
                self._log_metadata_summary("Metadata Update (Restarting Player)")
                self._is_restarting = True
                self._stop_kodi_playback()
                await asyncio.sleep(0.3)
                self._start_kodi_playback()
                self._is_restarting = False
            else:
                self._log_metadata_summary("Metadata Cached (Pre-play)")

    async def on_controller_state(self, payload):
        if payload and hasattr(payload, 'command'):
            try:
                if payload.command == PlayerCommand.VOLUME:
                    xbmc.executeJSONRPC(f'{{"jsonrpc":"2.0","method":"Application.SetVolume","params":{{"volume":{int(payload.volume)}}},"id":1}}')
                elif payload.command == PlayerCommand.MUTE:
                    xbmc.executeJSONRPC(f'{{"jsonrpc":"2.0","method":"Application.SetMute","params":{{"mute":{str(bool(payload.muted)).lower()}}},"id":1}}')
            except Exception: pass

    async def stream_handler(self, request):
        from aiohttp import web
        resp = web.StreamResponse(headers={
            "Content-Type": "audio/wav", 
            "Cache-Control": "no-cache",
            "Connection": "close"
        })
        await resp.prepare(request)
        try:
            await resp.write(create_wav_header())
            while not self._stop_event.is_set():
                try:
                    data = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                    if data is None: break
                    await resp.write(data)
                except asyncio.TimeoutError:
                    continue
        except Exception: pass
        return resp

async def run_service():
    proxy = AudioProxy()
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/stream.wav", proxy.stream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PROXY_PORT).start()
    asyncio.create_task(proxy.start_client())
    monitor = xbmc.Monitor()
    while not monitor.abortRequested(): await asyncio.sleep(1)
    if proxy.client: await proxy.client.disconnect()
    await runner.cleanup()

def main():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_service())
    except Exception: traceback.print_exc()

if __name__ == "__main__": main()