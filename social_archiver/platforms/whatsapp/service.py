"""WhatsApp's jobs as plain coroutines: what the queue worker invokes and the CLI wraps."""

import logging

from social_archiver.core.database import Database
from social_archiver.core.jobs import UploadJob, cleanup_downloads, run_jobs
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.whatsapp import config
from social_archiver.platforms.whatsapp.archiver import ArchiveJob
from social_archiver.core.caption import CaptionJob
from social_archiver.core.embed import IndexJob
from social_archiver.platforms.whatsapp.port import WhatsAppPort

logger = logging.getLogger(__name__)

PORT = WhatsAppPort()

MILVUS_COLLECTIONS = {
    "dm": "whatsapp_dm",
}


async def archive(fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
    config.validate_archive()
    tg = TelegramClient() if config.TELEGRAM_BOT_TOKEN else None
    async with Database(config.DATABASE_PATH) as db:
        await ArchiveJob(db, PORT, tg).run(fetch_all, category, retry_failed)


async def upload(retry_failed: bool = False):
    if not config.TELEGRAM_CHAT_DM:
        # Upload is deliberately unconfigured for private chats; a cycle enqueues this job
        # whenever the shared bot token exists, and nothing-to-do is not a failure.
        logger.info("No WHATSAPP_CHAT_DM configured; nothing to upload")
        return
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
    milvus = MilvusManager(uri=config.WHATSAPP_MILVUS_URI, collections=MILVUS_COLLECTIONS)
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
