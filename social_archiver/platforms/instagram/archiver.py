import asyncio
import logging
import traceback
from collections.abc import Iterator

from social_archiver.core.database import ArchiveStatus, Database, Item
from social_archiver.core.jobs import download_item, download_pending
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.likes import LikesFetcher
from social_archiver.platforms.instagram.fetchers.page import MediaPage
from social_archiver.platforms.instagram.fetchers.saved import SavedFetcher
from social_archiver.platforms.instagram.fetchers.shared import SharedFetcher
from social_archiver.platforms.instagram.port import PLATFORM, InstagramPort

logger = logging.getLogger(__name__)

CATEGORIES = ("likes", "saved", "shared")

# `saved` pages each collection separately, so one stored cursor cannot express where it
# is; the other two walk a single feed and resume exactly.
RESUMABLE = frozenset({"likes", "saved", "shared"})


class ArchiveJob:
    """Fetches liked/saved/DM-shared posts, records them, and downloads media to
    disk. Uploading and embedding are separate jobs.

    Each API page is committed and then downloaded before the next page is
    requested. That keeps a run cut short anywhere — rate limit, crash, Ctrl-C —
    from losing what it already fetched, and it downloads media while the signed
    CDN URLs are still fresh: those expire, and a stored URL cannot be renewed
    without refetching the post."""

    def __init__(self, ig_client: InstagramClient, db: Database, port: InstagramPort, tg: TelegramClient | None = None):
        self.db = db
        self.port = port
        self.tg = tg
        self.likes = LikesFetcher(ig_client)
        self.saved = SavedFetcher(ig_client)
        self.shared = SharedFetcher(ig_client)

    async def run(self, fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
        # Backlog first. Those URLs were minted by an earlier run and are the
        # closest to expiring; anything recorded below downloads inline instead.
        await download_pending(self.db, self.port, retry_failed)

        failures = []
        for name in CATEGORIES:
            if category not in (None, name):
                continue
            # DMs are the one category with no listing of its own: it needs to be told
            # whose thread to walk, so that setting is what enables it.
            if name == "shared" and not config.INSTAGRAM_DM_USERNAME:
                continue
            try:
                await self._archive_category(name, fetch_all)
            except Exception as e:
                logger.error(f"Archiving {name} failed: {e}", exc_info=True)
                if self.tg:
                    await self.tg.send_error_notification(type(e).__name__, f"archive:{name}", traceback.format_exc())
                failures.append(e)

        if failures:
            raise ExceptionGroup("archive failures", failures)

    async def _archive_category(self, category: str, fetch_all: bool):
        logger.info(f"Archiving {category} (fetch_all={fetch_all})")
        existing = await self.db.all_ids(PLATFORM)
        # Only a full backfill resumes: a plain run wants the newest items and would
        # otherwise clobber the backfill's position with a top-of-feed cursor.
        resumable = fetch_all and category in RESUMABLE
        start_cursor = await self.db.get_cursor(PLATFORM, category) if resumable else ""

        recorded = known = downloaded = 0
        pages = self._fetch(category, fetch_all, start_cursor)
        while True:
            # instagrapi is synchronous, so driving this generator inline blocks the event
            # loop: one stalled request stops every platform, not just Instagram.
            page = await asyncio.to_thread(next, pages, None)
            if page is None:
                break
            # Before the dedupe below, so a post moved between collections is still recorded
            # against the one it is in now. Only `saved` carries a collection.
            if memberships := [(m.pk, m.collection_name) for m in page.media if m.collection_name]:
                await self.db.add_collections(memberships)

            pending = []
            for media in page.media:
                if media.pk in existing:
                    known += 1
                    continue
                item = media.to_item(category)
                await self.db.insert(item)
                existing.add(media.pk)
                recorded += 1
                if item.archive_status is ArchiveStatus.PENDING:
                    pending.append(item)

            if resumable:
                await self.db.set_cursor(PLATFORM, category, page.cursor)

            downloaded += await self._download_page(pending)

            logger.info(f"{category}: {recorded} new, {downloaded} downloaded, {known} already archived")

        if resumable:
            await self.db.clear_cursor(PLATFORM, category)
        logger.info(f"Finished {category}: {recorded} new, {downloaded} downloaded, {known} already archived")

    async def _download_page(self, items: list[Item]) -> int:
        """Download a page's media several at a time. A reel is almost entirely time spent
        waiting on the network, so one at a time left the link idle: measured at 17 items a
        minute sequentially. Still per page, so urls are used while their signature is fresh."""
        semaphore = asyncio.Semaphore(config.DOWNLOAD_CONCURRENCY)

        async def download(item: Item) -> bool:
            async with semaphore:
                return await download_item(self.db, self.port, item)

        return sum(await asyncio.gather(*(download(item) for item in items)))

    def _fetch(self, category: str, fetch_all: bool, start_cursor: str = "") -> Iterator[MediaPage]:
        amount = 0 if fetch_all else config.FETCH_BATCH_SIZE
        match category:
            case "likes":
                return self.likes.fetch_liked_media(amount, start_cursor)
            case "saved":
                return self.saved.fetch_saved_media(amount, start_cursor)
            case "shared":
                return self.shared.fetch_shared_media(config.INSTAGRAM_DM_USERNAME, amount, start_cursor)
            case _:
                raise ValueError(f"Unknown category: {category}")
