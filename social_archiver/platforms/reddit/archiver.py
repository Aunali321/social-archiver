import logging
import traceback
from pathlib import Path

from social_archiver.core.database import Database
from social_archiver.core.jobs import download_pending
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.reddit import config
from social_archiver.platforms.reddit.client import RedditClient
from social_archiver.platforms.reddit.expander import ThreadExpander
from social_archiver.platforms.reddit.fetchers.export import SeedRef, parse_export
from social_archiver.platforms.reddit.fetchers.live import fetch_live
from social_archiver.platforms.reddit.port import PLATFORM, RedditPort
from social_archiver.platforms.reddit.simple_post import RedditItem

logger = logging.getLogger(__name__)

CATEGORIES = ("saved", "upvoted", "downvoted", "own")


class ArchiveJob:
    """Gathers seeds from the account export (full backfill) and the live listing
    (recent delta), expands each into bounded thread context, records every item,
    and downloads media. Uploading and embedding are separate, DB-driven jobs."""

    def __init__(self, client: RedditClient, db: Database, port: RedditPort, tg: TelegramClient | None = None):
        self.client = client
        self.db = db
        self.port = port
        self.tg = tg

    async def run(self, fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
        if retry_failed:
            await self._refresh_failed_media()
        export = self._load_export()
        for cat in CATEGORIES:
            if category not in (None, cat):
                continue
            try:
                await self._archive_category(cat, fetch_all)
                await self._backfill_from_export(cat, export.get(cat, []))
            except Exception as e:
                logger.error(f"Archiving {cat} failed: {e}", exc_info=True)
                if self.tg:
                    await self.tg.send_error_notification(type(e).__name__, f"archive:{cat}", traceback.format_exc())
        await download_pending(self.db, self.port, retry_failed)

    async def _refresh_failed_media(self):
        """Re-derive media urls for previously-failed items before retrying them. Those
        urls were captured when the item was first archived, so anything that has since
        moved, expired, or was recorded wrong can only recover by asking Reddit again."""
        failed = await self.db.failed_archive(PLATFORM)
        if not failed:
            return

        logger.info(f"Refreshing media urls for {len(failed)} previously-failed items")
        items = await self.client.hydrate([item.item_id for item in failed])
        changed = settled = 0
        for item in items:
            if not await self.db.refresh_media(item.fullname, item.media_urls, item.media_types, len(item.media)):
                continue
            changed += 1
            if not item.media:
                # Nothing left to derive, so there is nothing left to download: the item is
                # as complete as it can be. Leaving it 'failed' would requeue it every run.
                await self.db.mark_archived(item.fullname, [])
                settled += 1
        logger.info(
            f"Refreshed {changed} of {len(failed)} ({settled} had no media left to fetch, now archived; "
            f"rest unchanged or no longer resolvable)"
        )

    def _load_export(self) -> dict[str, list[SeedRef]]:
        """Seed references only. Hydration happens per chunk during the backfill, so an
        interrupted run keeps everything it already committed."""
        if not config.REDDIT_EXPORT_PATH:
            return {}
        path = Path(config.REDDIT_EXPORT_PATH)
        if not path.exists():
            logger.warning(f"REDDIT_EXPORT_PATH does not exist, skipping export ingest: {path}")
            return {}
        return parse_export(path)

    async def _archive_category(self, category: str, fetch_all: bool):
        known_ids = None if fetch_all else await self.db.ids_by_origin(PLATFORM, category)
        live = await fetch_live(self.client, category, known_ids)
        if not live:
            logger.info(f"No new {category} from the listing")
            return
        await self.db.add_categories((item.fullname, category) for item in live)
        await self._expand_and_record(category, live)

    async def _backfill_from_export(self, category: str, refs: list[SeedRef]):
        """Backfill from the account export in resumable chunks: it holds the complete
        saved/vote/own history, past the ~1000 the listing API returns. Reddit rate-limits,
        so a full pass cannot finish in one go — each chunk is hydrated, expanded and
        committed before the next, so a stopped run resumes from the database."""
        if not refs:
            return

        # Attribution first, for every ref the export lists under this category. The queue
        # below deliberately skips refs already archived — which is what lets it resume —
        # so membership recorded only there would miss anything a previous category's
        # thread expansion happened to pull in first.
        await self.db.add_categories((ref.fullname, category) for ref in refs)

        while True:
            existing = await self.db.all_ids(PLATFORM)
            queued = [ref for ref in refs if ref.fullname not in existing]
            if not queued:
                break
            chunk = queued[: config.REDDIT_EXPORT_BATCH]
            logger.info(f"Export backfill {category}: hydrating {len(chunk)} ({len(queued) - len(chunk)} still queued)")
            await self._expand_and_record(category, await self._hydrate_refs(category, chunk))

    async def _hydrate_refs(self, category: str, refs: list[SeedRef]) -> list[RedditItem]:
        """Every ref returns something, a tombstone when Reddit can no longer resolve it,
        so each chunk always shrinks the backfill queue."""
        by_fullname = {ref.fullname: ref for ref in refs}
        items = await self.client.hydrate(list(by_fullname))
        for item in items:
            item.origin = category

        found = {item.fullname for item in items}
        items.extend(_tombstone(ref, category) for ref in by_fullname.values() if ref.fullname not in found)
        return items

    async def _expand_and_record(self, category: str, seeds: list[RedditItem]):
        items = await ThreadExpander(self.client).expand(seeds)
        logger.info(f"Expanded {len(seeds)} {category} seeds into {len(items)} items")

        existing = await self.db.all_ids(PLATFORM)
        upgraded = sum(
            [
                await self.db.upgrade_origin(item.fullname, category)
                for item in items
                if item.origin == category and item.fullname in existing
            ]
        )
        if upgraded:
            logger.info(f"Upgraded origin to '{category}' for {upgraded} previously-discovered items")

        new_items = [item for item in items if item.fullname not in existing]
        logger.info(f"Recording {len(new_items)} new items ({len(items) - len(new_items)} already archived)")
        for item in new_items:
            await self.db.insert(item.to_item(category))


def _tombstone(ref: SeedRef, category: str) -> RedditItem:
    return RedditItem(
        fullname=ref.fullname,
        kind="post" if ref.fullname.startswith("t3_") else "comment",
        author="[deleted]",
        subreddit="",
        permalink=ref.permalink,
        origin=category,
        is_tombstone=True,
    )
