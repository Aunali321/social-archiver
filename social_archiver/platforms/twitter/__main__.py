import asyncio
import logging
import sys

from social_archiver.core.cli import build_parser
from social_archiver.core.config import ConfigError
from social_archiver.core.utils import setup_logging
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.service import archive, embed, run_all, source, upload
from social_archiver.platforms.twitter.sources import TwitterSourceFetcher

logger = logging.getLogger(__name__)


def main():
    args = build_parser(
        "Twitter/X archiver",
        categories=("likes", "bookmarks"),
        source_kinds=TwitterSourceFetcher.kinds,
    ).parse_args()
    setup_logging(config.LOG_FILE)
    logger.info(f"Twitter archiver: {args.command}")

    match args.command:
        case "archive":
            asyncio.run(archive(args.history, args.category, args.retry_failed))
        case "upload":
            asyncio.run(upload(args.retry_failed))
        case "embed":
            asyncio.run(embed(args.retry_failed, args.retry_refused))
        case "source":
            asyncio.run(source(args.target, args.kind, args.full, args.no_media))
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
