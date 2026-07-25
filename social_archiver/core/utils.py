import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from social_archiver.core import config


def setup_logging(log_file: Path):
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=config.LOG_ROTATION_DAYS)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"))

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # Both log per request/statement, and asyncprawcore dumps all 100 fullnames of every
    # batch hydrate — gigabytes over an export backfill, drowning our own INFO lines.
    logging.getLogger("asyncprawcore").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    # instagrapi logs whole response bodies at DEBUG. One DM thread page carries 75 xma
    # payloads and a media/infos/ reply carries 50 full media objects, so those lines run to
    # ~120KB each: 635MB of log for 1,166 items before this was silenced.
    logging.getLogger("instagrapi").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # `private_request` is left at INFO deliberately: it logs one short line per request,
    # carrying the url and its cursor, which is the only way to see how far a walk has got.
