import logging

from social_archiver.core.scheduler import DaemonScheduler
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.processor import Processor

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, processor: Processor):
        self.processor = processor
        self._daemon = DaemonScheduler(self.run_once, config.CHECK_INTERVAL_MINUTES)

    async def run_once(self, fetch_all: bool = False, categories: list[str] | None = None):
        logger.info("Running single iteration")
        for category in categories or ["likes", "saved", "shared"]:
            await self.processor.process_category(category, fetch_all)
        logger.info("Completed iteration")

    def run_daemon(self):
        self._daemon.run_daemon()

    def stop(self):
        self._daemon.stop()
