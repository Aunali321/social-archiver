import asyncio
import logging
import sys

from social_archiver.core.cli import build_parser
from social_archiver.core.config import ConfigError
from social_archiver.core.utils import setup_logging
from social_archiver.platforms.whatsapp import config
from social_archiver.platforms.whatsapp.archiver import CATEGORIES
from social_archiver.platforms.whatsapp.service import archive, caption, embed, run_all, upload

logger = logging.getLogger(__name__)


def main():
    args = build_parser("WhatsApp archiver", categories=CATEGORIES).parse_args()
    setup_logging(config.LOG_FILE)
    logger.info(f"WhatsApp archiver: {args.command}")

    match args.command:
        case "archive":
            asyncio.run(archive(args.history, args.category, args.retry_failed))
        case "upload":
            asyncio.run(upload(args.retry_failed))
        case "caption":
            asyncio.run(caption(args.retry_failed, args.retry_refused, args.limit))
        case "embed":
            asyncio.run(embed(args.retry_failed))
        case "run":
            asyncio.run(run_all(args.history))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    except ConfigError as e:
        logger.error(str(e))
        sys.exit(1)
