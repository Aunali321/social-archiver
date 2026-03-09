import argparse
import asyncio
import sys
from insta_archiver import config
from insta_archiver.utils import setup_logging
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.telegram_client import TelegramClient
from insta_archiver.database import Database
from insta_archiver.downloader import MediaDownloader
from insta_archiver.processor import Processor
from insta_archiver.scheduler import Scheduler
import logging

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Instagram to Telegram Archiver")

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

    parser.add_argument(
        "--category",
        choices=["saved", "likes", "shared"],
        help="Process only this category (saved, likes, or shared)",
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

    logger.info("Starting Instagram Archiver")

    ig_client = InstagramClient()
    tg_client = TelegramClient()

    try:
        ig_client.login()
    except Exception as e:
        logger.error(f"Failed to login to Instagram: {e}")
        sys.exit(1)

    async with Database() as db:
        downloader = MediaDownloader(ig_client)

        # Initialize embedding system if enabled
        embedding_processor = None
        milvus_manager = None
        if config.EMBEDDING_ENABLED:
            # Determine which VLM provider to use
            vlm_provider = config.VLM_PROVIDER.lower()
            api_key_missing = False

            if vlm_provider == "gemini":
                if not config.GEMINI_API_KEY:
                    logger.error("VLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
                    print(
                        "\nERROR: VLM_PROVIDER=gemini but GEMINI_API_KEY is not set\n",
                        file=sys.stderr,
                    )
                    api_key_missing = True
            else:  # openrouter (default)
                if not config.OPENROUTER_API_KEY:
                    logger.error(
                        "EMBEDDING_ENABLED is true but OPENROUTER_API_KEY is not set"
                    )
                    print(
                        "\nERROR: EMBEDDING_ENABLED is true but OPENROUTER_API_KEY is not set\n",
                        file=sys.stderr,
                    )
                    api_key_missing = True

            if not api_key_missing:
                try:
                    from insta_archiver.milvus_manager import MilvusManager
                    from insta_archiver.embedding_processor import EmbeddingProcessor

                    logger.info(
                        "Initializing embedding system (VLM + local embeddings)"
                    )

                    # Initialize the appropriate VLM client based on provider
                    if vlm_provider == "gemini":
                        from insta_archiver.gemini_client import GeminiClient

                        vlm_client = GeminiClient(
                            api_key=config.GEMINI_API_KEY,
                            model=config.GEMINI_MODEL,
                            timeout=config.EMBEDDING_TIMEOUT,
                        )
                        vlm_model_name = config.GEMINI_MODEL
                    else:  # openrouter
                        from insta_archiver.vlm_client import VLMClient

                        vlm_client = VLMClient(
                            api_key=config.OPENROUTER_API_KEY,
                            vlm_model=config.VLM_MODEL,
                            timeout=config.EMBEDDING_TIMEOUT,
                        )
                        vlm_model_name = config.VLM_MODEL

                    milvus_manager = MilvusManager(uri=config.INSTAGRAM_MILVUS_URI)
                    milvus_manager.initialize_collections()
                    embedding_processor = EmbeddingProcessor(vlm_client, milvus_manager)
                    logger.info(
                        f"Embedding system initialized: provider={vlm_provider}, model={vlm_model_name}, local embeddings"
                    )
                except Exception as e:
                    error_msg = f"Failed to initialize embedding system: {e}"
                    logger.error(error_msg)
                    logger.warning("Continuing without embeddings")
                    print(
                        f"\n ERROR: {error_msg}\n  Continuing without embeddings\n",
                        file=sys.stderr,
                    )
                    # Send error notification to Telegram
                    try:
                        await tg_client.send_error_notification(error_msg)
                    except Exception as notify_error:
                        logger.error(
                            f"Failed to send error notification: {notify_error}"
                        )

        processor = Processor(ig_client, tg_client, db, downloader, embedding_processor)
        scheduler = Scheduler(processor)

        # Determine which categories to process
        categories = [args.category] if args.category else None

        try:
            if args.init:
                logger.info(
                    "Running --init: fetching full history then starting daemon"
                )
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
