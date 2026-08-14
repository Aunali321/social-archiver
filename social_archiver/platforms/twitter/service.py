"""Twitter's jobs as plain coroutines: what the queue worker invokes and the CLI wraps."""

import logging

from social_archiver.core.database import Database
from social_archiver.core.jobs import UploadJob, cleanup_downloads, run_jobs
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.core.sources import SourceJob, SourceRef
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.archiver import ArchiveJob
from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.embedder import EmbedJob
from social_archiver.platforms.twitter.port import TwitterPort
from social_archiver.platforms.twitter.sources import TwitterSourceFetcher

logger = logging.getLogger(__name__)

PORT = TwitterPort()

MILVUS_COLLECTIONS = {"likes": "twitter_likes", "bookmarks": "twitter_bookmarks"}


async def archive(fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
    config.validate_archive()
    tw_client = TwitterClient()
    try:
        if not await tw_client.verify_credentials():
            raise RuntimeError("Twitter credential verification failed")
        tg = TelegramClient() if config.TELEGRAM_BOT_TOKEN else None
        async with Database(config.DATABASE_PATH) as db:
            await ArchiveJob(tw_client, db, PORT, tg).run(fetch_all, category, retry_failed)
    finally:
        await tw_client.close()


async def source(target: str, kind: str = "profile", full: bool = False, no_media: bool = False):
    config.validate_archive()
    tw_client = TwitterClient()
    try:
        if not await tw_client.verify_credentials():
            raise RuntimeError("Twitter credential verification failed")
        async with Database(config.DATABASE_PATH) as db:
            job = SourceJob(db, PORT, TwitterSourceFetcher(tw_client))
            ref = SourceRef(PORT.platform, kind, target)
            # A full walk deliberately ignores what is held, so re-running one repairs an
            # account whose earlier walk was cut short.
            since = None if full else await job.watermark_after(ref)
            await job.run(ref, since, download=not no_media)
    finally:
        await tw_client.close()


async def upload(retry_failed: bool = False):
    config.validate_upload()
    async with Database(config.DATABASE_PATH) as db:
        await UploadJob(db, TelegramClient(), PORT).run(retry_failed)
        await cleanup_downloads(db, PORT)


async def embed(retry_failed: bool = False, retry_refused: bool = False):
    config.validate_embed()
    vlm_client, model_name = create_vlm_client(config.VLM_PROVIDER)
    logger.info(f"Embedding with provider={config.VLM_PROVIDER}, model={model_name}")

    milvus = MilvusManager(uri=config.TWITTER_MILVUS_URI, collections=MILVUS_COLLECTIONS)
    milvus.initialize_collections()
    try:
        async with Database(config.DATABASE_PATH) as db:
            await EmbedJob(db, vlm_client, milvus, PORT).run(retry_failed, retry_refused)
            await cleanup_downloads(db, PORT)
    finally:
        milvus.close()


async def run_all(fetch_all: bool = False):
    jobs = {"archive": lambda: archive(fetch_all)}
    # Uploading and embedding are opt-in: without a bot token or an embedding server
    # there is nothing for them to do, and archiving does not depend on either.
    if config.TELEGRAM_BOT_TOKEN:
        jobs["upload"] = upload
    if config.EMBEDDING_ENABLED:
        jobs["embed"] = embed
    await run_jobs(jobs)
