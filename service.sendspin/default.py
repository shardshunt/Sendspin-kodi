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
    """The async lifecycle with improved window focusing and player detection"""
    # 1. Initialize your custom logger immediately
    log = logger.init_logger()
    log.info("--- Sendspin Persistent Session Starting ---")

    try:
        log.info("Initializing Docker backend...")
        await controller.setup()

        monitor = xbmc.Monitor()
        player = xbmc.Player()
        dummy_path = os.path.join(ADDON_PATH, "resources", "silent.mp3")

        # 2. Setup Metadata & Start Playback
        list_item = xbmcgui.ListItem("Sendspin Active")
        music_tag = list_item.getMusicInfoTag()
        music_tag.setTitle("Sendspin Audio")
        music_tag.setArtist("Docker System")

        log.info("Starting dummy playback to lock session...")
        player.play(dummy_path, list_item)

        # 3. Wait-state for active playback
        # This loop waits up to 5 seconds for the player to engage.
        # It prevents the 'ActivateWindow' command from firing too early.
        retries = 0
        while not player.isPlaying() and retries < 50:
            if monitor.abortRequested():
                return
            xbmc.sleep(100)
            retries += 1

        if player.isPlaying():
            log.info("Playback detected. Forcing focus to Music Visualization.")
            # 'ActivateWindow(visualisation)' is deterministic and avoids the
            # toggle behavior of 'Action(FullScreen)'.
            xbmc.executebuiltin("ActivateWindow(visualisation)")
        else:
            log.warning("Player failed to start within timeout; skipping focus.")

        log.info("Entering persistent audio loop. ALSA sink should be locked.")

        while not monitor.abortRequested():
            if not player.isPlaying():
                log.info("Playback stopped manually, exiting loop.")
                break

            if monitor.waitForAbort(1):
                break

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

    # Multi-instance guard to prevent overlapping Docker commands
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
