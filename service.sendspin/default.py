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


async def main_async(controller):
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
