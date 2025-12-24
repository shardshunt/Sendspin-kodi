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
        logger.info("Kodi player: playback started")
        if self._on_started: self._on_started()

    def onPlayBackStopped(self):
        logger.info("Kodi player: playback stopped")
        if self._on_stopped: self._on_stopped()

    def onPlayBackEnded(self):
        logger.info("Kodi player: playback ended")
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
    except Exception:
        pass

    class KodiHandler(logging.Handler):
        def emit(self, record):
            try:
                level = xbmc.LOGINFO if record.levelno < 40 else xbmc.LOGERROR
                xbmc.log(f"[Sendspin] {self.format(record)}", level)
            except Exception:
                pass

    kh = KodiHandler()
    kh.setFormatter(fmt)
    logger.addHandler(kh)
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
        self.log.setLevel(logging.DEBUG)
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

        # NEW FLAG: prevents first-track stale metadata
        self._have_started_playback = False

        self.log.info("AudioProxy initialized")

    def on_kodi_playback_started(self):
        self._kodi_is_playing = True
        self.log.info("Detected Kodi playback started")

    def on_kodi_playback_stopped(self):
        self._kodi_is_playing = False
        self._is_active = False
        self.log.info("Detected Kodi playback stopped; clearing properties and marking inactive")
        try:
            xbmcgui.Window(10000).clearProperty('Sendspin.Title')
            xbmcgui.Window(10000).clearProperty('Sendspin.Artist')
            xbmcgui.Window(10000).clearProperty('Sendspin.Art')
        except Exception:
            self.log.exception("Failed to clear window properties on stop")

    def _start_kodi_playback(self, force=False):
        if self._kodi_is_playing and not force:
            self.log.debug("Kodi already playing; _start_kodi_playback no-op")
            return

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
            self.log.info("Started Kodi playback for Sendspin stream: %s (force=%s)", title, force)
        except Exception:
            self.log.exception("Failed to start player")

    async def _stop_kodi_playback(self):
        self._is_active = False
        self._stop_event.set()
        self.log.info("Stopping Kodi playback and notifying subscribers")
        for q in list(self._subscribers):
            try:
                await q.put(None)
            except Exception:
                self.log.debug("Failed to notify a subscriber during stop")
        try:
            if self._player.isPlayingAudio():
                self._player.stop()
                self.log.info("Kodi player stop() called")
        except Exception:
            self.log.exception("Error while stopping Kodi player")

    async def start_client(self):
        from aiosendspin.client import SendspinClient
        from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
        from aiosendspin.models.types import Roles, AudioCodec

        while True:
            try:
                self.log.info("Preparing Sendspin client hello and attempting connection to %s", SERVER_URL)
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
                self.log.info("Sendspin client connected")
                while self.client and self.client.connected:
                    await asyncio.sleep(1)
                self.log.info("Sendspin client disconnected; will attempt reconnect")
            except Exception:
                self.log.exception("Sendspin client connection error; retrying in 5s")
                await asyncio.sleep(5)

    async def on_audio_chunk(self, ts, data, fmt):
        for q in list(self._subscribers):
            if not q.full():
                try:
                    await q.put(data)
                except Exception:
                    self.log.debug("Failed to enqueue audio chunk to a subscriber")

    async def on_stream_start(self, msg):
        self.log.info("Stream start message received from server (waiting for metadata)")
        # Playback is driven by metadata

    async def on_metadata(self, payload):
        if not payload or not hasattr(payload, "metadata"):
            self.log.debug("Received metadata payload with no metadata")
            return

        meta = payload.metadata

        def is_val(v):
            return v is not None and type(v).__name__ != 'UndefinedField'

        new_title = meta.title if is_val(meta.title) else "Unknown"
        new_artist = meta.artist if is_val(getattr(meta, 'artist', None)) else "Unknown"
        new_art = meta.artwork_url if hasattr(meta, 'artwork_url') and is_val(meta.artwork_url) else ""

        is_placeholder = (new_title == "Unknown" and new_artist == "Unknown")

        track_changed = (
            not is_placeholder and (
                self.current_metadata.get('title') != new_title or
                self.current_metadata.get('artist') != new_artist
            )
        )

        # FIRST METADATA PACKET AFTER STARTUP
        if not self._have_started_playback:
            self.log.info("First metadata received; starting playback with correct metadata")
            self._have_started_playback = True

            self.current_metadata['title'] = new_title
            self.current_metadata['artist'] = new_artist
            self.current_artwork_url = new_art

            self._start_kodi_playback(force=True)
            return

        # NORMAL TRACK CHANGE
        if track_changed:
            self.log.info("Track changed; restarting playback with new metadata")

            self.current_metadata['title'] = new_title
            self.current_metadata['artist'] = new_artist
            self.current_artwork_url = new_art

            self._start_kodi_playback(force=True)

        # Metadata changed but not a track change
        elif (
            self.current_metadata.get('title') != new_title or
            self.current_metadata.get('artist') != new_artist or
            self.current_artwork_url != new_art
        ):
            self.log.info("Metadata update: %s - %s (art=%s)", new_artist, new_title, bool(new_art))
            self.current_metadata['title'] = new_title
            self.current_metadata['artist'] = new_artist
            self.current_artwork_url = new_art

        # Update Kodi UI
        if self._is_active:
            try:
                win = xbmcgui.Window(10000)
                win.setProperty('Sendspin.Title', new_title)
                win.setProperty('Sendspin.Artist', new_artist)
                if new_art:
                    win.setProperty('Sendspin.Art', new_art)
                self.log.debug("Window properties updated with new metadata")
            except Exception:
                self.log.exception("Failed to set window properties for metadata")

            try:
                if self._li:
                    tag = self._li.getMusicInfoTag()
                    tag.setTitle(new_title)
                    tag.setArtist(new_artist)
                    if new_art:
                        self._li.setArt({'thumb': new_art, 'icon': new_art, 'poster': new_art})
                    self.log.debug("ListItem metadata updated")
            except Exception:
                self.log.exception("Failed to update ListItem metadata")

            try:
                icon = new_art if new_art else "DefaultAudio.png"
                xbmc.executebuiltin(
                    f'Notification(Now Playing, {new_artist} - {new_title}, 5000, {icon})'
                )
                self.log.debug("Displayed Now Playing notification")
            except Exception:
                self.log.exception("Failed to show notification for metadata")

            try:
                playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
                pos = playlist.getposition()
                if pos >= 0:
                    it = playlist[pos]
                    it.setArt({'thumb': new_art})
                    it.setInfo('music', {'title': new_title, 'artist': new_artist})
                    self.log.debug("Playlist entry updated with new metadata")
            except Exception:
                self.log.debug("No playlist update performed or failed to update playlist")

    async def on_controller_state(self, payload):
        if payload and hasattr(payload, 'command'):
            self.log.info("Controller command received: %s", getattr(payload, 'command', '<unknown>'))
            try:
                if payload.command == PlayerCommand.VOLUME:
                    xbmc.executeJSONRPC(
                        f'{{"jsonrpc":"2.0","method":"Application.SetVolume","params":{{"volume":{int(payload.volume)}}},"id":1}}'
                    )
                    self.log.debug("Volume command applied: %s", int(payload.volume))
                elif "stop" in str(payload.command).lower():
                    self.log.info("Stop command received from controller; stopping playback")
                    await self._stop_kodi_playback()
            except Exception:
                self.log.exception("Failed to apply controller command")

    async def stream_handler(self, request):
        from aiohttp import web
        from aiohttp.client_exceptions import ClientConnectionResetError
        my_queue = asyncio.Queue(maxsize=10)
        self._subscribers.add(my_queue)
        self.log.info("New stream subscriber connected (total=%d)", len(self._subscribers))
        
        resp = web.StreamResponse(headers={"Content-Type": "audio/x-wav", "Cache-Control": "no-cache"})
        await resp.prepare(request)

        try:
            await resp.write(create_wav_header())
        except Exception:
            self._subscribers.discard(my_queue)
            self.log.info("Stream subscriber disconnected (total=%d)", len(self._subscribers))
            return resp

        try:
            while not self._stop_event.is_set():
                try:
                    data = await asyncio.wait_for(my_queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                if data is None:
                    break

                try:
                    await resp.write(data)
                except Exception:
                    break
        finally:
            self._subscribers.discard(my_queue)
            self.log.info("Stream subscriber disconnected (total=%d)", len(self._subscribers))

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
    site = web.TCPSite(runner, "127.0.0.1", PROXY_PORT)
    await site.start()
    logger.info("HTTP proxy started on 127.0.0.1:%d", PROXY_PORT)
    
    client_task = asyncio.create_task(PROXY_INSTANCE.start_client())
    logger.info("Sendspin client task started")

    monitor = xbmc.Monitor()
    try:
        while not monitor.abortRequested():
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Service monitor cancelled")
    finally:
        client_task.cancel()
        logger.info("Shutting down service: cleaning up HTTP runner")
        await runner.cleanup()
        logger.info("Service shutdown complete")

if __name__ == "__main__":
    try:
        logger.info("Starting sendspin service")
        asyncio.run(run_service())
    except Exception:
        logger.exception("Unhandled exception in sendspin service")
        try:
            traceback.print_exc()
        except Exception:
            pass
