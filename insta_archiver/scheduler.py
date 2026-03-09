import logging
import asyncio
import schedule
import time
from insta_archiver import config
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.telegram_client import TelegramClient
from insta_archiver.database import Database
from insta_archiver.downloader import MediaDownloader
from insta_archiver.processor import Processor

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, processor: Processor):
        self.processor = processor
        self.running = False

    async def run_once(self, fetch_all: bool = False, categories: list = None):
        logger.info("Running single iteration")

        # Default to all categories if none specified
        if categories is None:
            categories = ["likes", "saved", "shared"]

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
