import argparse
import asyncio
import sys
from twitter_archiver import config
from twitter_archiver.utils import setup_logging
from twitter_archiver.twitter_client import TwitterClient
from twitter_archiver.telegram_client import TelegramClient
from twitter_archiver.database import Database
from twitter_archiver.downloader import MediaDownloader
from twitter_archiver.processor import Processor
from twitter_archiver.scheduler import Scheduler
import logging

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Twitter/X to Telegram Archiver")

    parser.add_argument("--once", action="store_true", help="Run once and exit")

    parser.add_argument(
        "--history", action="store_true", help="Download all historical content"
    )

    parser.add_argument(
        "--init",
        action="store_true",
        help="First-time setup: fetch full history then switch to daemon mode",
    )

    parser.add_argument(
        "--daemon", action="store_true", help="Run in daemon mode (default)"
    )

    return parser.parse_args()


async def main():
    setup_logging()

    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    args = parse_args()

    logger.info("Starting Twitter Archiver")

    tw_client = TwitterClient()
    tg_client = TelegramClient()

    # Verify Twitter credentials
    try:
        if not await tw_client.verify_credentials():
            logger.error("Failed to verify Twitter credentials")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to verify Twitter credentials: {e}")
        sys.exit(1)

    async with Database() as db:
        downloader = MediaDownloader()

        # Initialize embedding system if enabled
        embedding_processor = None
        milvus_manager = None
        if config.EMBEDDING_ENABLED:
            vlm_provider = config.VLM_PROVIDER.lower()
            api_key_missing = False

            if vlm_provider == "vertex":
                pass  # uses ADC (GOOGLE_APPLICATION_CREDENTIALS), no API key needed
            elif vlm_provider == "gemini":
                if not config.GEMINI_API_KEY:
                    logger.error("VLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
                    api_key_missing = True
            else:
                if not config.OPENROUTER_API_KEY:
                    logger.error(
                        "EMBEDDING_ENABLED is true but OPENROUTER_API_KEY is not set"
                    )
                    api_key_missing = True

            if not api_key_missing:
                try:
                    from twitter_archiver.milvus_manager import MilvusManager
                    from twitter_archiver.embedding_processor import EmbeddingProcessor

                    logger.info(
                        "Initializing embedding system (VLM + local embeddings)"
                    )

                    if vlm_provider == "vertex":
                        from insta_archiver.vertex_client import VertexVLMClient

                        vlm_client = VertexVLMClient(
                            model=config.VERTEX_MODEL,
                            project=config.VERTEX_PROJECT,
                            location=config.VERTEX_LOCATION,
                            timeout=config.EMBEDDING_TIMEOUT,
                        )
                        vlm_model_name = config.VERTEX_MODEL
                    elif vlm_provider == "gemini":
                        from insta_archiver.gemini_client import GeminiClient

                        vlm_client = GeminiClient(
                            api_key=config.GEMINI_API_KEY,
                            model=config.GEMINI_MODEL,
                            timeout=config.EMBEDDING_TIMEOUT,
                        )
                        vlm_model_name = config.GEMINI_MODEL
                    else:
                        from insta_archiver.vlm_client import VLMClient

                        vlm_client = VLMClient(
                            api_key=config.OPENROUTER_API_KEY,
                            vlm_model=config.VLM_MODEL,
                            timeout=config.EMBEDDING_TIMEOUT,
                        )
                        vlm_model_name = config.VLM_MODEL

                    milvus_manager = MilvusManager(uri=config.TWITTER_MILVUS_URI)
                    milvus_manager.initialize_collections()
                    embedding_processor = EmbeddingProcessor(vlm_client, milvus_manager, db)
                    logger.info(
                        f"Embedding system initialized: provider={vlm_provider}, model={vlm_model_name}"
                    )
                except Exception as e:
                    error_msg = f"Failed to initialize embedding system: {e}"
                    logger.error(error_msg)
                    logger.warning("Continuing without embeddings")
                    try:
                        await tg_client.send_error_notification(
                            error_type=type(e).__name__,
                            context="embedding_init",
                            traceback=str(e),
                        )
                    except Exception:
                        pass

        processor = Processor(tw_client, tg_client, db, downloader, embedding_processor)
        scheduler = Scheduler(processor)

        try:
            if args.init:
                logger.info(
                    "Running --init: fetching full history then starting daemon"
                )
                await processor.process_likes(fetch_all=True)
                logger.info("History fetch complete. Starting daemon mode...")
                scheduler.run_daemon()
            elif args.once:
                await processor.process_likes(fetch_all=args.history)
            elif args.history:
                logger.info("Running history mode")
                await processor.process_likes(fetch_all=True)
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
