#!/usr/bin/env python3

import os, sys, traceback, time
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

PROXY_INSTANCE = None
PLAYER_INSTANCE = None

# --- KODI PLAYER LOGIC ---

class Player(xbmc.Player):
    def __init__(self, **kwargs):
        super().__init__()
        self._on_started = kwargs.get('on_playback_started')
        self._on_stopped = kwargs.get('on_playback_stopped')

    def onPlayBackStarted(self):
        if self._on_started: self._on_started()

    def onPlayBackStopped(self):
        if self._on_stopped: self._on_stopped()

    def onPlayBackEnded(self):
        if self._on_stopped: self._on_stopped()

# --- LOGGING SETUP ---

addon = xbmcaddon.Addon()
LOG_PATH = addon.getSetting("log_path") or "/storage/.kodi/temp/sendspin-service.log"

def setup_logging():
    logger = logging.getLogger("sendspin")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    
    try:
        fh = logging.FileHandler(LOG_PATH)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception: pass

    class KodiHandler(logging.Handler):
        def emit(self, record):
            try:
                level = xbmc.LOGINFO if record.levelno < 40 else xbmc.LOGERROR
                xbmc.log(f"[Sendspin] {self.format(record)}", level)
            except Exception: pass
    kh = KodiHandler(); kh.setFormatter(fmt); logger.addHandler(kh)
    logger.propagate = False
    return logger

logger = setup_logging()

# --- CONFIGURATION & UTILITIES ---

PROXY_PORT = 59999
SERVER_URL = addon.getSetting("server_url") or "ws://192.168.0.161:8927/sendspin"
CLIENT_ID = "kodi-sendspin-client"
CLIENT_NAME = "Kodi Room"

def create_wav_header(sample_rate=48000, channels=2, bits=16):
    return (b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + b"fmt " +
            struct.pack("<I", 16) + struct.pack("<H", 1) +
            struct.pack("<H", channels) + struct.pack("<I", sample_rate) +
            struct.pack("<I", sample_rate * channels * (bits // 8)) + 
            struct.pack("<H", channels * (bits // 8)) +
            struct.pack("<H", bits) + b"data" + b"\xff\xff\xff\xff")

# --- MAIN AUDIO PROXY ---

class AudioProxy:
    def __init__(self):
        self.log = logging.getLogger("sendspin.proxy")
        self._subscribers = set()
        self.client = None
        self._player = Player(
            on_playback_started=self.on_kodi_playback_started,
            on_playback_stopped=self.on_kodi_playback_stopped
        )
        self.current_metadata = {'title': 'Sendspin Stream', 'artist': 'Sendspin'}
        self.current_artwork_url = ""
        self._stop_event = asyncio.Event()
        self._is_active = False
        self._kodi_is_playing = False
        self._li = None 

    def on_kodi_playback_started(self):
        self._kodi_is_playing = True

    def on_kodi_playback_stopped(self):
        self._kodi_is_playing = False
        self._is_active = False
        # Clear window properties on stop
        xbmcgui.Window(10000).clearProperty('Sendspin.Title')
        xbmcgui.Window(10000).clearProperty('Sendspin.Artist')
        xbmcgui.Window(10000).clearProperty('Sendspin.Art')

    def _start_kodi_playback(self):
        if self._kodi_is_playing: return

        stream_url = f"http://127.0.0.1:{PROXY_PORT}/stream.wav"
        title = self.current_metadata.get('title')
        
        self._li = xbmcgui.ListItem(label=title)
        self._li.setPath(stream_url)
        self._li.setProperty('IsPlayable', 'true')
        
        tag = self._li.getMusicInfoTag()
        tag.setTitle(title)
        tag.setArtist(self.current_metadata.get('artist'))
        tag.setMediaType('music')
        
        if self.current_artwork_url:
            self._li.setArt({'thumb': self.current_artwork_url, 'icon': self.current_artwork_url})

        try:
            self._stop_event.clear()
            self._player.play(item=stream_url, listitem=self._li)
            self._is_active = True
        except Exception:
            logger.exception("Failed to start player")

    async def _stop_kodi_playback(self):
        self._is_active = False
        self._stop_event.set()
        for q in list(self._subscribers):
            try: await q.put(None)
            except: pass
        if self._player.isPlayingAudio():
            self._player.stop()

    async def start_client(self):
        from aiosendspin.client import SendspinClient
        from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
        from aiosendspin.models.types import Roles, AudioCodec

        while True:
            try:
                ps = ClientHelloPlayerSupport(
                    supported_formats=[SupportedAudioFormat(codec=AudioCodec.PCM, channels=2, sample_rate=48000, bit_depth=16)],
                    buffer_capacity=48000 * 2 * 2,
                    supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE]
                )
                self.client = SendspinClient(CLIENT_ID, CLIENT_NAME, [Roles.PLAYER, Roles.CONTROLLER, Roles.METADATA], player_support=ps)
                self.client.set_audio_chunk_listener(self.on_audio_chunk)
                self.client.set_stream_start_listener(self.on_stream_start)
                self.client.set_metadata_listener(self.on_metadata)
                self.client.set_controller_state_listener(self.on_controller_state)

                await self.client.connect(SERVER_URL)
                while self.client and self.client.connected: await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(5)

    async def on_audio_chunk(self, ts, data, fmt):
        for q in list(self._subscribers):
            if not q.full(): await q.put(data)

    async def on_stream_start(self, msg):
        self._start_kodi_playback()

    async def on_metadata(self, payload):
        if not payload or not hasattr(payload, "metadata"): return
        meta = payload.metadata
        def is_val(v): return v is not None and type(v).__name__ != 'UndefinedField'

        new_title = meta.title if is_val(meta.title) else "Unknown"
        new_artist = meta.artist if is_val(getattr(meta, 'artist', None)) else "Unknown"
        new_art = meta.artwork_url if hasattr(meta, 'artwork_url') and is_val(meta.artwork_url) else ""
        
        if self.current_metadata.get('title') != new_title or self.current_artwork_url != new_art:
            self.current_metadata['title'] = new_title
            self.current_metadata['artist'] = new_artist
            self.current_artwork_url = new_art
            
            if self._is_active:
                # 1. Update Window Properties (Skin fallbacks)
                win = xbmcgui.Window(10000) # Home window is global
                win.setProperty('Sendspin.Title', new_title)
                win.setProperty('Sendspin.Artist', new_artist)
                if new_art: win.setProperty('Sendspin.Art', new_art)

                # 2. Update the stored ListItem
                if self._li:
                    tag = self._li.getMusicInfoTag()
                    tag.setTitle(new_title)
                    tag.setArtist(new_artist)
                    if new_art:
                        self._li.setArt({'thumb': new_art, 'icon': new_art, 'poster': new_art})
                
                # 3. Pass metadata using notification
                icon = new_art if new_art else "DefaultAudio.png"
                xbmc.executebuiltin(f'Notification(Now Playing, {new_artist} - {new_title}, 5000, {icon})')
                
                # 4. Attempt Playlist Update
                try:
                    playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
                    pos = playlist.getposition()
                    if pos >= 0:
                        it = playlist[pos]
                        it.setArt({'thumb': new_art})
                        it.setInfo('music', {'title': new_title, 'artist': new_artist})
                except: pass

    async def on_controller_state(self, payload):
        if payload and hasattr(payload, 'command'):
            if payload.command == PlayerCommand.VOLUME:
                xbmc.executeJSONRPC(f'{{"jsonrpc":"2.0","method":"Application.SetVolume","params":{{"volume":{int(payload.volume)}}},"id":1}}')
            elif "stop" in str(payload.command).lower():
                await self._stop_kodi_playback()

    async def stream_handler(self, request):
        from aiohttp import web
        my_queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(my_queue)
        
        resp = web.StreamResponse(headers={"Content-Type": "audio/x-wav", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        await resp.write(create_wav_header())
        
        try:
            while not self._stop_event.is_set():
                data = await asyncio.wait_for(my_queue.get(), timeout=2.0)
                if data is None: break
                await resp.write(data)
        except: pass
        finally:
            self._subscribers.discard(my_queue)
        return resp

# --- SERVICE ENTRY POINT ---

async def run_service():
    global PROXY_INSTANCE, PLAYER_INSTANCE
    PROXY_INSTANCE = AudioProxy()
    PLAYER_INSTANCE = PROXY_INSTANCE._player
    
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/stream.wav", PROXY_INSTANCE.stream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PROXY_PORT).start()
    
    client_task = asyncio.create_task(PROXY_INSTANCE.start_client())
    
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        await asyncio.sleep(1)
        
    client_task.cancel()
    await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(run_service())
    except Exception:
        traceback.print_exc()