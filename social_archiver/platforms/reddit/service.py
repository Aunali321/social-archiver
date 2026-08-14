"""Reddit's jobs as plain coroutines: what the queue worker invokes and the CLI wraps."""

import logging
from pathlib import Path

from social_archiver.core.database import Database
from social_archiver.core.jobs import UploadJob, cleanup_downloads, run_jobs
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.core.sources import SourceJob, SourceRef
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.reddit import config
from social_archiver.platforms.reddit.archiver import ArchiveJob
from social_archiver.platforms.reddit.client import RedditClient
from social_archiver.core.caption import CaptionJob
from social_archiver.core.embed import IndexJob
from social_archiver.platforms.reddit.port import RedditPort
from social_archiver.platforms.reddit.sources import RedditSourceFetcher

logger = logging.getLogger(__name__)

PORT = RedditPort()

MILVUS_COLLECTIONS = {
    "saved": "reddit_saved",
    "upvoted": "reddit_upvoted",
    "downvoted": "reddit_downvoted",
    "own": "reddit_own",
}


async def archive(fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
    config.validate_archive()
    client = RedditClient()
    try:
        logger.info(f"Authenticated as u/{await client.verify()}")
        tg = TelegramClient() if config.TELEGRAM_BOT_TOKEN else None
        async with Database(config.DATABASE_PATH) as db:
            await ArchiveJob(client, db, PORT, tg).run(fetch_all, category, retry_failed)
    finally:
        await client.close()


async def source(target: str, kind: str = "subreddit", full: bool = False, no_media: bool = False):
    """`full` has no effect here: the dump is a local file, so every run reads all of it and
    dedupes by id. There is no paging to stop early."""
    config.validate_source()
    async with Database(config.DATABASE_PATH) as db:
        job = SourceJob(db, PORT, RedditSourceFetcher(Path(config.REDDIT_DUMP_DIR)))
        await job.run(SourceRef(PORT.platform, kind, target), download=not no_media)


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
    milvus = MilvusManager(uri=config.REDDIT_MILVUS_URI, collections=MILVUS_COLLECTIONS)
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
