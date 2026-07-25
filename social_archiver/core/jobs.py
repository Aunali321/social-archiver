import asyncio
import logging
import traceback
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from telegram.error import RetryAfter

from social_archiver.core import config
from social_archiver.core.database import Database, Item
from social_archiver.core.downloader import download_urls, download_with_retry
from social_archiver.core.telegram_client import TelegramClient

logger = logging.getLogger(__name__)

_PROGRESS_EVERY = 100


class PlatformPort(Protocol):
    """Platform-specific pieces the shared jobs need."""

    platform: str
    chats: dict[str, int]  # category -> Telegram chat id (0 = not configured)

    def caption(self, item: Item) -> str: ...
    def downloads_folder(self, item: Item) -> Path: ...
    def upload_order(self, item: Item) -> tuple: ...


async def ensure_media(db: Database, port: PlatformPort, item: Item) -> list[Path]:
    """Return the item's media files, re-downloading from the stored URLs when
    they are no longer on disk. Raises when the media can't be restored."""
    if item.local_paths and all(p.exists() for p in item.local_paths):
        return item.local_paths
    if not item.media_urls:
        if item.has_media:
            raise RuntimeError(f"{item.item_id} has media but no stored URLs to restore it from")
        return []

    logger.info(f"Downloading media for {item.item_id} ({len(item.media_urls)} file(s))")
    paths = await download_with_retry(
        lambda: download_urls(item.media_urls, port.downloads_folder(item), item.item_id),
        item.item_id,
        config.MAX_DOWNLOAD_RETRIES,
        config.RETRY_BACKOFF_BASE,
    )
    await db.mark_archived(item.item_id, paths)
    return paths


async def download_item(db: Database, port: PlatformPort, item: Item) -> bool:
    """Download one item's media, recording a failed attempt instead of raising,
    so one dead media never stops the run. Returns whether it landed."""
    try:
        await ensure_media(db, port, item)
        return True
    except Exception as e:
        attempt = item.archive_attempts + 1
        logger.error(f"Download failed for {item.item_id} (attempt {attempt}/{config.MAX_ARCHIVE_ATTEMPTS}): {e}")
        await db.mark_archive_failed(item.item_id, str(e))
        return False


async def download_pending(db: Database, port: PlatformPort, retry_failed: bool = False):
    """Download media for every recorded item that still needs it, several at a time:
    a single video costs tens of seconds in yt-dlp and ffmpeg, almost all of it spent
    waiting, so downloading one at a time leaves the machine idle. An item that fails
    MAX_ARCHIVE_ATTEMPTS times is left alone until --retry-failed."""
    if retry_failed:
        reset = await db.reset_archive_failures(port.platform)
        logger.info(f"Cleared the attempt count on {reset} previously-failed items")

    pending = await db.pending_archive(port.platform, config.MAX_ARCHIVE_ATTEMPTS)
    if not pending:
        return

    total = len(pending)
    logger.info(f"Downloading media for {total} items, {config.DOWNLOAD_CONCURRENCY} at a time")
    semaphore = asyncio.Semaphore(config.DOWNLOAD_CONCURRENCY)
    completed = 0

    async def download(item: Item):
        nonlocal completed
        async with semaphore:
            await download_item(db, port, item)
        completed += 1
        if completed % _PROGRESS_EVERY == 0 or completed == total:
            logger.info(f"Downloaded {completed}/{total} items")

    await asyncio.gather(*(download(item) for item in pending))


class UploadJob:
    """Sends archived-but-not-yet-uploaded items to Telegram. Purely DB-driven,
    so it can run any time after (or concurrently with) archiving."""

    def __init__(self, db: Database, tg: TelegramClient, port: PlatformPort):
        self.db = db
        self.tg = tg
        self.port = port

    async def run(self, retry_failed: bool = False):
        for category, chat_id in self.port.chats.items():
            if not chat_id:
                continue
            items = await self.db.pending_upload(self.port.platform, category, retry_failed)
            if not items:
                continue

            items.sort(key=self.port.upload_order)
            logger.info(f"Uploading {len(items)} {category} items")
            for item in items:
                try:
                    await self._upload(item, chat_id)
                except RetryAfter as e:
                    logger.warning(f"Flood control (retry after {e.retry_after}s); stopping, will resume next run")
                    return
                except Exception as e:
                    logger.error(f"Upload failed for {item.item_id}: {e}")
                    await self.db.mark_upload_failed(item.item_id, str(e))
                    await self.tg.send_error_notification(
                        type(e).__name__, f"upload:{item.item_id}", traceback.format_exc()
                    )

    async def _upload(self, item: Item, chat_id: int):
        paths = await ensure_media(self.db, self.port, item)
        caption = self.port.caption(item)
        message_ids = (
            await self.tg.send_media(chat_id, paths, caption) if paths else await self.tg.send_text(chat_id, caption)
        )
        await self.db.mark_uploaded(item.item_id, message_ids)
        logger.info(f"Uploaded {item.item_id} (@{item.author_username})")


async def cleanup_downloads(db: Database, port: PlatformPort):
    """Delete local media once every enabled consumer (upload, and embed when
    enabled) is done with it. Until then files stay on disk so the jobs can
    run independently, in any order, at any time."""
    if not config.CLEANUP_DOWNLOADS:
        return

    items = await db.cleanup_candidates(port.platform, require_embed=config.EMBEDDING_ENABLED)
    for item in items:
        for path in item.local_paths:
            path.unlink(missing_ok=True)
        await db.clear_local_paths(item.item_id)
    if items:
        logger.info(f"Cleaned up downloads for {len(items)} items")


async def run_jobs(jobs: dict[str, Callable[[], Awaitable[None]]]):
    """Run jobs in order; a failure in one never blocks the others."""
    errors = []
    for name, job in jobs.items():
        try:
            await job()
        except Exception as e:
            logger.error(f"{name} job failed: {e}", exc_info=True)
            errors.append(e)
    if errors:
        raise ExceptionGroup("job failures", errors)
