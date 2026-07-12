import argparse
import asyncio
import logging
import sys

from social_archiver.core.database import Database
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.core.utils import setup_logging
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.downloader import MediaDownloader
from social_archiver.platforms.twitter.processor import Processor
from social_archiver.platforms.twitter.scheduler import Scheduler

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Twitter/X to Telegram Archiver")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--history", action="store_true", help="Download all historical content")
    parser.add_argument("--init", action="store_true", help="First-time setup: fetch full history then switch to daemon mode")
    parser.add_argument("--daemon", action="store_true", help="Run in daemon mode (default)")
    return parser.parse_args()


async def init_embedding_system(tg_client: TelegramClient):
    if not config.EMBEDDING_ENABLED:
        return None, None

    try:
        from social_archiver.core.milvus_manager import MilvusManager
        from social_archiver.platforms.twitter.embedding_processor import EmbeddingProcessor

        vlm_client, vlm_model_name = create_vlm_client(config.VLM_PROVIDER)

        milvus_manager = MilvusManager(
            uri=config.TWITTER_MILVUS_URI,
            collections={"likes": "twitter_likes", "bookmarks": "twitter_bookmarks"},
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
        except Exception:
            pass
        return None, None


async def main():
    setup_logging(config.LOG_FILE)

    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    args = parse_args()
    logger.info("Starting Twitter Archiver")

    tw_client = TwitterClient()
    tg_client = TelegramClient()

    try:
        if not await tw_client.verify_credentials():
            logger.error("Failed to verify Twitter credentials")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to verify Twitter credentials: {e}")
        sys.exit(1)

    async with Database(config.DATABASE_PATH) as db:
        downloader = MediaDownloader()
        embedding_processor, milvus_manager = await init_embedding_system(tg_client)

        processor = Processor(tw_client, tg_client, db, downloader, embedding_processor)
        scheduler = Scheduler(processor)

        try:
            if args.init:
                logger.info("Running --init: fetching full history then starting daemon")
                await processor.process_all(fetch_all=True)
                logger.info("History fetch complete. Starting daemon mode...")
                scheduler.run_daemon()
            elif args.once:
                await processor.process_all(fetch_all=args.history)
            elif args.history:
                logger.info("Running history mode")
                await processor.process_all(fetch_all=True)
            else:
                scheduler.run_daemon()
        finally:
            await tw_client.close()
            if milvus_manager:
                milvus_manager.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
        sys.exit(0)
