import asyncio
import os
import sys

import xbmcgui

# Setup paths for embedded libraries[cite: 5]
addon_dir = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_dir, "resources", "lib")
if os.path.isdir(lib_path) and lib_path not in sys.path:
    sys.path.insert(0, lib_path)
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

import logger  # noqa: E402
from service import SendspinServiceController  # noqa: E402


def run_script():
    # Initialize logger first to ensure startup is captured
    log = logger.init_logger()
    log.info("--- Sendspin Script Starting ---")

    service = SendspinServiceController()
    loop = asyncio.new_event_loop()

    try:
        # Run setup and start background tasks[cite: 5]
        loop.run_until_complete(service.setup())

        # Display a modal dialog to keep the script running
        dialog = xbmcgui.Dialog()
        log.info("Displaying 'Running' dialog to user.")

        # This will block the script here until the user clicks "OK"[cite: 2]
        dialog.ok(
            "Sendspin",
            "Sendspin is active and the Docker container is running.\n\nClick OK to stop playback and restore audio.",
        )

    except Exception as e:
        log.error(f"Script encountered an error: {e}")
    finally:
        # Ensure cleanup always runs[cite: 5]
        log.info("Starting cleanup and audio restoration.")
        loop.run_until_complete(service.cleanup())
        loop.close()
        log.info("--- Sendspin Script Finished ---")


if __name__ == "__main__":
    run_script()
