import argparse
import asyncio
import logging
import sys

from social_archiver.core.database import Database
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.core.utils import setup_logging
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.downloader import MediaDownloader
from social_archiver.platforms.instagram.processor import Processor
from social_archiver.platforms.instagram.scheduler import Scheduler

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Instagram to Telegram Archiver")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--history", action="store_true", help="Download all historical content")
    parser.add_argument("--init", action="store_true", help="First-time setup: fetch full history then switch to daemon mode")
    parser.add_argument("--daemon", action="store_true", help="Run in daemon mode (default)")
    parser.add_argument("--category", choices=["saved", "likes", "shared"], help="Process only this category")
    return parser.parse_args()


async def init_embedding_system(tg_client: TelegramClient):
    if not config.EMBEDDING_ENABLED:
        return None, None

    try:
        from social_archiver.core.milvus_manager import MilvusManager
        from social_archiver.platforms.instagram.embedding_processor import EmbeddingProcessor

        vlm_client, vlm_model_name = create_vlm_client(config.VLM_PROVIDER)

        milvus_manager = MilvusManager(
            uri=config.INSTAGRAM_MILVUS_URI,
            collections={"likes": "instagram_likes", "saved": "instagram_saved", "shared": "instagram_shared"},
        )
        milvus_manager.initialize_collections()
        logger.info(f"Embedding system initialized: provider={config.VLM_PROVIDER}, model={vlm_model_name}")
        return EmbeddingProcessor(vlm_client, milvus_manager), milvus_manager

    except Exception as e:
        error_msg = f"Failed to initialize embedding system: {e}"
        logger.error(error_msg)
        logger.warning("Continuing without embeddings")
        try:
            await tg_client.send_error_notification(type(e).__name__, "embedding_init", str(e))
        except Exception as notify_error:
            logger.error(f"Failed to send error notification: {notify_error}")
        return None, None


async def main():
    setup_logging(config.LOG_FILE)

    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    args = parse_args()
    logger.info("Starting Instagram Archiver")

    ig_client = InstagramClient()
    tg_client = TelegramClient()

    try:
        ig_client.login()
    except Exception as e:
        logger.error(f"Failed to login to Instagram: {e}")
        sys.exit(1)

    async with Database(config.DATABASE_PATH) as db:
        downloader = MediaDownloader()
        embedding_processor, milvus_manager = await init_embedding_system(tg_client)

        processor = Processor(ig_client, tg_client, db, downloader, embedding_processor)
        scheduler = Scheduler(processor)
        categories = [args.category] if args.category else None

        try:
            if args.init:
                logger.info("Running --init: fetching full history then starting daemon")
                await scheduler.run_once(fetch_all=True, categories=categories)
                logger.info("History fetch complete. Starting daemon mode...")
                scheduler.run_daemon()
            elif args.once:
                await scheduler.run_once(fetch_all=args.history, categories=categories)
            elif args.history:
                logger.info("Running history mode")
                await scheduler.run_once(fetch_all=True, categories=categories)
            else:
                scheduler.run_daemon()
        finally:
            if milvus_manager:
                milvus_manager.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
        sys.exit(0)
