import os
import sys
from urllib.parse import parse_qs

import xbmcaddon
import xbmcplugin

import xbmc

ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo("path")

LIB_PATH = os.path.join(ADDON_PATH, "resources", "lib")
if os.path.isdir(LIB_PATH) and LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

import logger  # noqa: E402
from service import SendspinServiceController  # noqa: E402


def handle_plugin_action(controller: SendspinServiceController) -> bool:
    try:
        query = sys.argv[2].lstrip("?")
    except IndexError:
        return False

    action = parse_qs(query).get("action", [""])[0]
    if not action:
        return False

    xbmc.log(f"[Sendspin] Handling plugin action: {action}", xbmc.LOGINFO)

    actions = {
        "play": controller.send_play,
        "pause": controller.send_pause,
        "playpause": controller.send_play_pause,
        "toggle_play_pause": controller.send_play_pause,
        "next": controller.send_next_track,
        "previous": controller.send_previous_track,
    }
    handler = actions.get(action)
    if handler is None:
        xbmc.log(f"[Sendspin] Unknown plugin action: {action}", xbmc.LOGWARNING)
        return True

    handler()
    return True


if __name__ == "__main__":
    logger.init_logger()

    try:
        handle = int(sys.argv[1])
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
    except (IndexError, ValueError):
        pass

    handle_plugin_action(SendspinServiceController())
