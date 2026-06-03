import asyncio
import os

import logger
import xbmcaddon
import xbmcgui
from service import SendspinServiceController

import xbmc

ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo("path")


def is_setting_enabled(name: str, default: bool = True) -> bool:
    value = ADDON.getSetting(name)
    if value == "":
        return default
    return value.lower() == "true"


class SendspinKodiPlayer(xbmc.Player):
    def __init__(self, controller: SendspinServiceController):
        super().__init__()
        self.controller = controller

    def onPlayBackPaused(self):  # noqa: N802 - Kodi callback name
        self.controller.handle_kodi_pause()

    def onPlayBackResumed(self):  # noqa: N802 - Kodi callback name
        self.controller.handle_kodi_resume()


def get_state_volume(state: dict) -> tuple[int, bool] | None:
    volume_payload = state.get("volume")
    if not isinstance(volume_payload, dict):
        return None

    sendspin_volume = volume_payload.get("volume")
    muted = volume_payload.get("muted", False)

    if sendspin_volume is None:
        return None

    try:
        return max(0, min(100, int(sendspin_volume))), bool(muted)
    except (TypeError, ValueError):
        return None


def is_sendspin_active(track_info: dict, playback_state: dict, audio_state: dict) -> bool:
    if audio_state.get("stream_active", False):
        return True
    try:
        if float(playback_state.get("speed", 0)) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if not track_info:
        return False
    try:
        return float(playback_state.get("speed", 0)) > 0
    except (TypeError, ValueError):
        return False


async def run_session(controller: SendspinServiceController):
    """Kodi service lifecycle that claims the audio device only while Sendspin is active."""
    log = logger.init_logger()
    log.info("--- Sendspin Service Session Starting ---")

    try:
        log.info("Initializing Docker backend...")
        await controller.setup()

        monitor = xbmc.Monitor()
        player = SendspinKodiPlayer(controller)
        dummy_path = os.path.join(ADDON_PATH, "resources", "silent.mp3")

        rewind_before_end_seconds = 5.0
        poll_interval_seconds = 0.5
        volume_poll_interval_seconds = 1.0
        last_volume_poll_time = 0.0
        last_seen_sendspin_volume_state = None
        last_seen_kodi_volume_state = None
        last_seen_title = None
        current_duration = 0
        audio_claimed = False

        list_item = xbmcgui.ListItem("Sendspin Active")
        music_tag = list_item.getMusicInfoTag()
        music_tag.setTitle("Sendspin Audio")
        music_tag.setArtist("Docker System")

        async def start_playback():
            log.info("Starting dummy playback track...")
            player.play(dummy_path, list_item)
            await asyncio.sleep(1.0)
            # Route Kodi's playback stream to virtual null-sink to release physical audio hardware
            await asyncio.get_running_loop().run_in_executor(None, controller.route_kodi_to_null_sink)
            await asyncio.get_running_loop().run_in_executor(None, controller.suspend_physical_sinks, True)
            xbmc.executebuiltin("Dialog.Close(all, true)")
            if is_setting_enabled("activate_visualisation_enabled"):
                xbmc.executebuiltin("ActivateWindow(visualisation)")

        log.info("Entering Sendspin service loop. Audio will be claimed on active playback.")

        while not monitor.abortRequested():
            was_audio_claimed_before = audio_claimed
            loop_time = asyncio.get_running_loop().time()
            sendspin_state = await asyncio.get_running_loop().run_in_executor(None, controller.get_sendspin_state)
            track_info = {}
            playback_state = {}
            audio_state = {}
            sendspin_volume_state = None

            if sendspin_state:
                track_info = sendspin_state.get("track") or {}
                playback_state = sendspin_state.get("playback") or {}
                audio_state = sendspin_state.get("audio") or {}
                sendspin_volume_state = get_state_volume(sendspin_state)

            has_track = bool(track_info)
            audio_active = audio_state.get("stream_active", False)
            try:
                speed = float(playback_state.get("speed", 0))
            except (TypeError, ValueError):
                speed = 0.0
            is_playing = audio_active or speed > 0.0

            # Sync playback and audio device state
            is_kodi_playing = False
            if player.isPlaying():
                try:
                    is_kodi_playing = "silent.mp3" in player.getPlayingFile()
                except Exception as e:
                    log.warning(f"Could not read playing file: {e}")
            is_kodi_paused = xbmc.getCondVisibility("Player.Paused") if is_kodi_playing else False

            if not has_track:
                # State 3: Stopped/Idle
                if is_kodi_playing:
                    log.info("Sendspin idle: stopping dummy playback and releasing audio.")
                    controller.suppress_kodi_player_events()
                    player.stop()
                if audio_claimed:
                    controller.release_sendspin_audio_to_kodi()
                    audio_claimed = False
            else:
                # State 1 or 2: Playing or Paused
                if is_playing:
                    if not audio_claimed:
                        log.info("Sendspin playing: acquiring audio device.")
                        if controller.original_kodi_device and "alsa" in controller.original_kodi_device.lower():
                            controller._switch_to_alternate()
                            await asyncio.sleep(1.5)  # Wait for Kodi to release the ALSA device
                        if controller.acquire_sendspin_audio():
                            audio_claimed = True
                            await asyncio.sleep(0.5)  # Give Docker a smidge to lock the hardware device
                            log.info("Playback active; resetting stream to get headers...")
                            controller.send_pause()
                            await asyncio.sleep(0.3)
                            controller.send_play()
                        else:
                            log.warning("Failed to acquire Sendspin audio device.")

                    if not is_kodi_playing:
                        if is_setting_enabled("require_dummy_playback") and was_audio_claimed_before:
                            log.info("Dummy playback stopped by user; pausing Sendspin and releasing audio.")
                            controller.send_pause()
                            controller.release_sendspin_audio_to_kodi()
                            audio_claimed = False
                        else:
                            log.info("Sendspin playing: starting dummy playback...")
                            await start_playback()
                            is_kodi_playing = True
                            is_kodi_paused = False

                    if is_kodi_playing and is_kodi_paused:
                        log.info("Sendspin playing: resuming Kodi dummy playback.")
                        controller.suppress_kodi_player_events()
                        player.pause()
                else:
                    # State 2: Paused
                    if audio_claimed:
                        log.info("Sendspin paused: releasing audio device back to Kodi.")
                        controller.release_sendspin_audio_to_kodi()
                        audio_claimed = False

                    if is_kodi_playing and not is_kodi_paused:
                        log.info("Sendspin paused: pausing Kodi dummy playback.")
                        controller.suppress_kodi_player_events()
                        player.pause()

            # Handle volume sync
            if loop_time - last_volume_poll_time >= volume_poll_interval_seconds:
                last_volume_poll_time = loop_time
                kodi_volume_state = controller.get_kodi_volume_state()

                if last_seen_sendspin_volume_state is None:
                    last_seen_sendspin_volume_state = sendspin_volume_state
                elif (
                    audio_claimed
                    and sendspin_volume_state is not None
                    and sendspin_volume_state != last_seen_sendspin_volume_state
                ):
                    last_seen_sendspin_volume_state = sendspin_volume_state
                    sendspin_volume, sendspin_muted = sendspin_volume_state
                    kodi_volume = controller.apply_sendspin_volume_to_kodi(sendspin_volume, sendspin_muted)
                    kodi_volume_state = controller.get_kodi_volume_state()
                    last_seen_kodi_volume_state = kodi_volume_state
                    log.info(
                        "Applied Sendspin volume to Kodi: "
                        f"sendspin_volume={sendspin_volume} muted={sendspin_muted} kodi_volume={kodi_volume}"
                    )

                if last_seen_kodi_volume_state is None:
                    last_seen_kodi_volume_state = kodi_volume_state
                elif audio_claimed and kodi_volume_state != last_seen_kodi_volume_state:
                    last_seen_kodi_volume_state = kodi_volume_state
                    mapped_volume = controller.apply_kodi_volume_to_sendspin(kodi_volume_state)
                    log.info(
                        "Kodi volume changed: "
                        f"kodi_volume={kodi_volume_state['volume']} "
                        f"muted={kodi_volume_state['muted']} "
                        f"mapped_sendspin_volume={mapped_volume}"
                    )

                current_delay_ms = await asyncio.get_running_loop().run_in_executor(
                    None, controller.get_delay_ms_setting
                )
                if current_delay_ms != controller._last_applied_delay_ms:
                    if controller.set_sendspin_delay(current_delay_ms):
                        log.info("Applied Sendspin delay from Kodi setting: %sms", current_delay_ms)

            # Handle metadata updates
            if has_track and track_info:
                title = track_info.get("title")
                artist = track_info.get("artist") or "Unknown Artist"
                album = track_info.get("album") or "Unknown Album"

                if title and title != last_seen_title:
                    list_item.setLabel(title)

                    tag = list_item.getMusicInfoTag()
                    tag.setMediaType("song")
                    tag.setTitle(title)
                    tag.setArtist(artist)
                    tag.setAlbum(album)

                    list_item.setInfo("music", {"title": title, "artist": artist, "album": album, "mediatype": "song"})

                    log.info(f"Track changed to: {tag.getArtist()} - {tag.getTitle()} ({tag.getAlbum()})")

                    thumb = track_info.get("artwork_url")
                    if thumb:
                        list_item.setArt({"thumb": thumb})

                    if is_playing:
                        await start_playback()
                    last_seen_title = title

            # Handle seeking and duration sync
            if has_track and playback_state and is_kodi_playing:
                position = playback_state.get("position", 0)
                duration = playback_state.get("duration", 0)

                if duration > 0 and duration != current_duration:
                    player.getMusicInfoTag().setDuration(int(duration))
                    current_duration = duration

                current_kodi_pos = player.getTime()
                if abs(current_kodi_pos - position) > 1.0:
                    player.seekTime(position)

            # Dummy track EOF loop/rewind check
            if is_kodi_playing:
                try:
                    total_time = player.getTotalTime()
                    current_time = player.getTime()
                except RuntimeError as e:
                    log.warning(f"Could not read dummy playback position: {e}")
                    await asyncio.sleep(poll_interval_seconds)
                    continue

                if total_time > rewind_before_end_seconds:
                    remaining_time = total_time - current_time
                    if remaining_time <= rewind_before_end_seconds:
                        log.info(
                            f"Dummy track nearing EOF ({current_time:.1f}/{total_time:.1f}s). Seeking back to start."
                        )
                        player.seekTime(0)
                        await asyncio.sleep(1.0)
                        continue

            await asyncio.sleep(poll_interval_seconds)

    except Exception as e:
        log.error(f"Async loop encountered an error: {e}")
    finally:
        log.info("Starting cleanup and audio restoration.")
        await controller.cleanup()
        log.info("--- Sendspin Service Session Finished ---")
