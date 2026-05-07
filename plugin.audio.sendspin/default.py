import asyncio
import os
import sys

import xbmcaddon
import xbmcgui
import xbmcplugin

import xbmc

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_PATH = ADDON.getAddonInfo("path")

# Setup paths for embedded libraries
LIB_PATH = os.path.join(ADDON_PATH, "resources", "lib")
if os.path.isdir(LIB_PATH) and LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

import logger  # noqa: E402
from service import SendspinServiceController  # noqa: E402


async def main_async(controller: SendspinServiceController):
    """The async lifecycle with dummy playback kept alive by pre-EOF seeking."""
    log = logger.init_logger()
    log.info("--- Sendspin Persistent Session Starting ---")

    try:
        log.info("Initializing Docker backend...")
        await controller.setup()

        monitor = xbmc.Monitor()
        player = xbmc.Player()
        dummy_path = os.path.join(ADDON_PATH, "resources", "silent.mp3")

        # Future improvement: make this dummy track long-lived, e.g. around an hour,
        # so normal Sendspin sessions do not need frequent seek resets.
        rewind_before_end_seconds = 5.0
        poll_interval_seconds = 0.5
        volume_poll_interval_seconds = 1.0
        last_volume_poll_time = 0.0
        last_seen_sendspin_volume = None
        last_seen_kodi_volume_state = None
        last_seen_title = None
        current_duration = 0

        # Setup Metadata
        list_item = xbmcgui.ListItem("Sendspin Active")
        music_tag = list_item.getMusicInfoTag()
        music_tag.setTitle("Sendspin Audio")
        music_tag.setArtist("Docker System")

        async def start_playback():
            """Force restart playback and GUI focus, safely yielding to the event loop."""
            log.info("Starting dummy playback track...")
            player.play(dummy_path, list_item)

            # Non-blocking delay to allow Kodi to initialize the ALSA sink
            await asyncio.sleep(1.0)

            # Clear modal dialogs that block window activation
            xbmc.executebuiltin("Dialog.Close(all, true)")
            xbmc.executebuiltin("ActivateWindow(visualisation)")

        # Initial Playback
        await start_playback()

        log.info("Entering persistent audio loop. ALSA sink is locked.")

        while not monitor.abortRequested():
            loop_time = asyncio.get_running_loop().time()
            if loop_time - last_volume_poll_time >= volume_poll_interval_seconds:
                last_volume_poll_time = loop_time
                sendspin_volume = await asyncio.get_running_loop().run_in_executor(
                    None,
                    controller.get_sendspin_volume,
                )
                kodi_volume_state = controller.get_kodi_volume_state()

                if last_seen_sendspin_volume is None:
                    last_seen_sendspin_volume = sendspin_volume
                elif sendspin_volume is not None and sendspin_volume != last_seen_sendspin_volume:
                    last_seen_sendspin_volume = sendspin_volume
                    kodi_volume = controller.apply_sendspin_volume_to_kodi(sendspin_volume)
                    kodi_volume_state = controller.get_kodi_volume_state()
                    last_seen_kodi_volume_state = kodi_volume_state
                    log.info(
                        f"Applied Sendspin volume to Kodi: sendspin_volume={sendspin_volume} kodi_volume={kodi_volume}"
                    )

                if last_seen_kodi_volume_state is None:
                    last_seen_kodi_volume_state = kodi_volume_state
                elif kodi_volume_state != last_seen_kodi_volume_state:
                    last_seen_kodi_volume_state = kodi_volume_state
                    mapped_volume = controller.apply_kodi_volume_to_sendspin(kodi_volume_state)
                    log.info(
                        "Kodi volume changed: "
                        f"kodi_volume={kodi_volume_state['volume']} "
                        f"muted={kodi_volume_state['muted']} "
                        f"mapped_sendspin_volume={mapped_volume}"
                    )

            track_info = controller.get_latest_track_info()
            playback_state = controller.get_latest_playback_state()

            # --- HANDLER 1: TRACK METADATA ---
            if track_info:
                title = track_info.get("title") or "Unknown Title"
                artist = track_info.get("artist") or "Unknown Artist"
                album = track_info.get("album") or "Unknown Album"

                # Only trigger a UI refresh if the title actually changed
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
                    else:
                        # Clear old artwork if the new track doesn't have any
                        list_item.setArt({"thumb": "", "poster": "", "fanart": ""})

                    await start_playback()
                    last_seen_title = title

            # --- HANDLER 2: PLAYBACK STATE ---
            if playback_state and player.isPlaying():
                position = playback_state.get("position", 0) + 2  # Add 2 seconds to account for delay
                duration = playback_state.get("duration", 0)
                speed = playback_state.get("speed", 1)

                if duration > 0 and duration != current_duration:
                    player.getMusicInfoTag().setDuration(int(duration))
                    current_duration = duration

                current_kodi_pos = player.getTime()
                if abs(current_kodi_pos - position) > 2.0:
                    player.seekTime(position)

                # 3. Handle Play/Pause State
                if speed == 0 and player.isPlaying():
                    player.pause()
                elif speed > 0 and not player.isPlaying():
                    player.pause()

            if not player.isPlaying():
                log.info("Dummy playback stopped by user/intervention. Exiting loop.")
                break

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
                    # Future improvement: when Sendspin exposes active track metadata,
                    # seek this dummy player to match the real song position instead.
                    log.info(f"Dummy track nearing EOF ({current_time:.1f}/{total_time:.1f}s). Seeking back to start.")
                    player.seekTime(0)
                    await asyncio.sleep(1.0)
                    continue

            await asyncio.sleep(poll_interval_seconds)

    except Exception as e:
        log.error(f"Async loop encountered an error: {e}")
    finally:
        log.info("Starting cleanup and audio restoration.")
        await controller.cleanup()
        log.info("--- Sendspin Persistent Session Finished ---")


if __name__ == "__main__":
    # Prevent GUI hangs by resolving the directory call immediately
    try:
        handle = int(sys.argv[1])
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
    except (IndexError, ValueError):
        pass

    # Multi-instance guard to prevent multiple controllers fighting for ALSA
    win = xbmcgui.Window(10000)
    if win.getProperty(f"{ADDON_ID}.running") == "true":
        sys.exit()

    win.setProperty(f"{ADDON_ID}.running", "true")

    try:
        controller = SendspinServiceController()
        asyncio.run(main_async(controller))
    except Exception as e:
        xbmc.log(f"[Sendspin] Fatal Startup Error: {e}", xbmc.LOGERROR)
    finally:
        win.clearProperty(f"{ADDON_ID}.running")
