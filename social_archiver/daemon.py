"""The scheduler and workers with no HTTP surface. Schedules stay editable in data/jobs.db."""

import argparse
import asyncio
import logging

from social_archiver.core import config
from social_archiver.core.config import ConfigError
from social_archiver.core.queue import JobQueue
from social_archiver.core.utils import setup_logging
from social_archiver.core.worker import PLATFORMS, running

logger = logging.getLogger(__name__)


async def run(platforms: tuple[str, ...]):
    async with running(JobQueue(config.DATA_DIR / "jobs.db"), platforms):
        await asyncio.Event().wait()


def main():
    parser = argparse.ArgumentParser(
        description="Run the scheduler and job workers without the web UI",
        epilog="A platform must be served by exactly one process. Splitting platforms across "
        "daemons is fine; running two that both cover a platform is not.",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=PLATFORMS,
        metavar="NAME",
        help=f"Serve only this platform; repeatable. One of {', '.join(PLATFORMS)}. Defaults to all",
    )
    args = parser.parse_args()
    platforms = tuple(dict.fromkeys(args.platform)) if args.platform else PLATFORMS

    setup_logging(config.LOGS_DIR / "daemon.log")
    logger.info(f"Social Archiver daemon: {', '.join(platforms)}")
    asyncio.run(run(platforms))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    except ConfigError as e:
        logger.error(str(e))
        raise SystemExit(1) from e
