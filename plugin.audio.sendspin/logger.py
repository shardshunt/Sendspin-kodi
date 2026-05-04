import logging

import xbmc


def init_logger():
    logger = logging.getLogger("sendspin")
    logger.setLevel(logging.DEBUG)

    class KodiHandler(logging.Handler):
        def emit(self, record):
            xbmc.log(f"[Sendspin] {self.format(record)}", xbmc.LOGINFO)

    kh = KodiHandler()
    logger.addHandler(kh)
    return logger
