"""Instagram's jobs as plain coroutines: what the queue worker invokes and the CLI wraps."""

import asyncio
import logging

from social_archiver.core.database import Database
from social_archiver.core.jobs import UploadJob, cleanup_downloads, run_jobs
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.archiver import ArchiveJob
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.core.embed import IndexJob
from social_archiver.platforms.instagram.embedder import CaptionJob
from social_archiver.platforms.instagram.port import InstagramPort

logger = logging.getLogger(__name__)

PORT = InstagramPort()

MILVUS_COLLECTIONS = {"likes": "instagram_likes", "saved": "instagram_saved", "shared": "instagram_shared"}


async def archive(fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
    config.validate_archive()
    ig_client = InstagramClient()
    await asyncio.to_thread(ig_client.login)
    async with Database(config.DATABASE_PATH) as db:
        tg = TelegramClient() if config.TELEGRAM_BOT_TOKEN else None
        await ArchiveJob(ig_client, db, PORT, tg).run(fetch_all, category, retry_failed)


async def upload(retry_failed: bool = False):
    config.validate_upload()
    async with Database(config.DATABASE_PATH) as db:
        await UploadJob(db, TelegramClient(), PORT).run(retry_failed)
        await cleanup_downloads(db, PORT)


async def caption(retry_failed: bool = False, retry_refused: bool = False, limit: int | None = None):
    config.validate_caption()
    vlm_client, model_name = create_vlm_client(config.VLM_PROVIDER)
    logger.info(f"Captioning with provider={config.VLM_PROVIDER}, model={model_name}")
    async with Database(config.DATABASE_PATH) as db:
        await CaptionJob(db, vlm_client, PORT).run(retry_failed, retry_refused, limit)
        await cleanup_downloads(db, PORT)


async def embed(retry_failed: bool = False):
    config.validate_embed()
    milvus = MilvusManager(uri=config.INSTAGRAM_MILVUS_URI, collections=MILVUS_COLLECTIONS)
    milvus.initialize_collections()
    try:
        async with Database(config.DATABASE_PATH) as db:
            await IndexJob(db, milvus, PORT).run(retry_failed)
            await cleanup_downloads(db, PORT)
    finally:
        milvus.close()


async def run_all(fetch_all: bool = False):
    jobs = {"archive": lambda: archive(fetch_all)}
    if config.TELEGRAM_BOT_TOKEN:
        jobs["upload"] = upload
    if config.CAPTIONING_ENABLED:
        jobs["caption"] = caption
    if config.EMBEDDING_ENABLED:
        jobs["embed"] = embed
    await run_jobs(jobs)
