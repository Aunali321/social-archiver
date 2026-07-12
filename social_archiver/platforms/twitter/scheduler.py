import logging

from social_archiver.core.scheduler import DaemonScheduler
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.processor import Processor

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, processor: Processor):
        self.processor = processor
        self._daemon = DaemonScheduler(self.run_once, config.CHECK_INTERVAL_MINUTES)

    async def run_once(self, fetch_all: bool = False):
        logger.info("Running single iteration")
        await self.processor.process_all(fetch_all=fetch_all)
        logger.info("Completed iteration")

    def run_daemon(self):
        self._daemon.run_daemon()

    def stop(self):
        self._daemon.stop()
