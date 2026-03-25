import logging
import asyncio
import schedule
import time
from twitter_archiver import config
from twitter_archiver.processor import Processor

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, processor: Processor):
        self.processor = processor
        self.running = False

    async def run_once(self, fetch_all: bool = False, categories: list = None):
        logger.info("Running single iteration")

        if categories is None:
            categories = []
            if config.TELEGRAM_CHAT_BOOKMARKS:
                categories.append("bookmarks")
            if config.TELEGRAM_CHAT_LIKES:
                categories.append("likes")

        for category in categories:
            await self.processor.process_category(category, fetch_all)

        logger.info("Completed iteration")

    def schedule_periodic(self):
        def job():
            asyncio.run(self.run_once())

        schedule.every(config.CHECK_INTERVAL_MINUTES).minutes.do(job)
        logger.info(f"Scheduled to run every {config.CHECK_INTERVAL_MINUTES} minutes")

    def run_daemon(self):
        self.schedule_periodic()
        self.running = True

        asyncio.run(self.run_once())

        logger.info("Entering daemon mode")
        while self.running:
            schedule.run_pending()
            time.sleep(1)

    def stop(self):
        self.running = False
