import logging

import xbmc


def init_logger():
    logger = logging.getLogger("sendspin")
    logger.setLevel(logging.DEBUG)

    if any(getattr(handler, "name", None) == "sendspin-kodi" for handler in logger.handlers):
        return logger

    class KodiHandler(logging.Handler):
        def emit(self, record):
            xbmc.log(f"[Sendspin] {self.format(record)}", xbmc.LOGINFO)

    kh = KodiHandler()
    kh.name = "sendspin-kodi"
    logger.addHandler(kh)
    return logger
