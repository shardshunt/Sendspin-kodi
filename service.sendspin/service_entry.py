import asyncio
import os
import sys

import xbmcaddon
import xbmcgui

import xbmc

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_PATH = ADDON.getAddonInfo("path")

LIB_PATH = os.path.join(ADDON_PATH, "resources", "lib")
if os.path.isdir(LIB_PATH) and LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

import logger  # noqa: E402
from service import SendspinServiceController  # noqa: E402
from session import run_session  # noqa: E402


def is_setting_enabled(name: str, default: bool = True) -> bool:
    value = ADDON.getSetting(name)
    if value == "":
        return default
    return value.lower() == "true"


def get_instance_guard_window():
    try:
        return xbmcgui.Window(10000)
    except RuntimeError as e:
        xbmc.log(f"[Sendspin] Multi-instance guard unavailable: {e}", xbmc.LOGWARNING)
        return None


def is_instance_running(win) -> bool:
    return win is not None and win.getProperty(f"{ADDON_ID}.running") == "true"


def set_instance_running(win) -> None:
    if win is not None:
        win.setProperty(f"{ADDON_ID}.running", "true")


def clear_instance_running(win) -> None:
    if win is not None:
        win.clearProperty(f"{ADDON_ID}.running")


if __name__ == "__main__":
    logger.init_logger()

    win = None
    if is_setting_enabled("instance_guard_enabled"):
        win = get_instance_guard_window()
        if is_instance_running(win):
            sys.exit()

        set_instance_running(win)

    try:
        asyncio.run(run_session(SendspinServiceController()))
    except Exception as e:
        xbmc.log(f"[Sendspin] Fatal Service Error: {e}", xbmc.LOGERROR)
    finally:
        clear_instance_running(win)
