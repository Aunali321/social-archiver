import asyncio
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from social_archiver.core.database import Database, Item
from social_archiver.core.jobs import download_pending
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.expander import TOMBSTONE_REASON_BATCH, TweetExpander
from social_archiver.platforms.twitter.fetchers.export import parse_like_export
from social_archiver.platforms.twitter.port import PLATFORM, TwitterPort
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)

FETCH_ATTEMPTS = 5
INITIAL_RETRY_DELAY = 180.0
MAX_RETRY_DELAY = 1800.0
PAGE_DELAY = 1.5

# Naming a refusal costs one TweetDetail request each, so a run only asks about this many and
# says how many it left. Answered ones carry their reason and are never asked about again.
TOMBSTONE_PROBE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    seed_origin: str


CATEGORIES = (Category("likes", "liked"), Category("bookmarks", "bookmarked"))


class ArchiveJob:
    """Fetches seed tweets, expands them into their full thread context,
    records every tweet, and downloads media to disk.

    Uploading and embedding are separate jobs. Rows are committed before any
    download starts, so an interrupted run resumes exactly where it stopped."""

    def __init__(self, tw_client: TwitterClient, db: Database, port: TwitterPort, tg: TelegramClient | None = None):
        self.tw_client = tw_client
        self.db = db
        self.port = port
        self.tg = tg

    async def run(self, fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
        # Backlog first. Those URLs were minted by an earlier run and are the closest to expiring.
        await download_pending(self.db, self.port, retry_failed)

        if retry_failed:
            await self._resolve_tombstones()

        failures = []
        for cat in CATEGORIES:
            if category not in (None, cat.name):
                continue
            try:
                await self._archive_category(cat, fetch_all)
            except Exception as e:
                failures.append(e)

        # Before raising, so a category that died part way through still keeps the media it
        # recorded, and one failing category does not strand the other's downloads.
        await download_pending(self.db, self.port, retry_failed)

        if failures:
            raise ExceptionGroup("archive failures", failures)

    async def _resolve_tombstones(self):
        """Look the tombstoned tweets up again.

        A tombstone records that this account, on this connection, was refused a tweet. That is
        not the same as the tweet being gone: withholding is per-country, so a tweet invisible
        from one exit resolves normally from another. One request per 100 ids is cheap enough to
        spend on finding out."""
        tombstoned = await self.db.tombstoned(PLATFORM)
        if not tombstoned:
            return

        by_id = {item.item_id: item for item in tombstoned}
        logger.info(f"Re-resolving {len(by_id)} tombstoned tweets")
        result = await self.tw_client.get_tweets_by_ids(list(by_id), page_delay=PAGE_DELAY)

        recovered: dict[str, list[dict[str, Any]]] = {}
        for raw in result.tweets:
            tweet = SimpleTweet.from_api_dict(raw)
            item = by_id.get(tweet.id)
            if item and await self.db.replace_tombstone(tweet.to_item(item.category)):
                recovered.setdefault(item.category, []).append(raw)
                logger.info(f"Recovered {tweet.id} from @{tweet.author_username}")

        for item_id, reason in result.unavailable.items():
            if item_id in by_id and reason != by_id[item_id].text:
                await self.db.set_tombstone_reason(item_id, reason)

        await self._name_refusals([item for tid, item in by_id.items() if tid in result.missing])

        for name, seeds in recovered.items():
            await self._expand_recovered(name, seeds)

        logger.info(f"Recovered {sum(len(s) for s in recovered.values())} of {len(by_id)} tombstoned tweets")

    async def _name_refusals(self, refused: list[Item]):
        """Ask why the still-refused tweets are refused.

        The batch endpoint returns a bare null for anything it will not serve, so a tweet withheld
        in this country is indistinguishable from a deleted one there. TweetDetail does say: it
        returns a tombstone naming the author and the country. That costs a request each, so only
        the ones still carrying no reason are asked about."""
        unnamed = [item for item in refused if item.text == TOMBSTONE_REASON_BATCH]
        if left := len(unnamed) - TOMBSTONE_PROBE_LIMIT:
            logger.info(f"{left} refusals left unnamed this run; rerun --retry-failed to continue")

        for item in unnamed[:TOMBSTONE_PROBE_LIMIT]:
            try:
                thread = await self.tw_client.get_thread(item.item_id, page_delay=PAGE_DELAY)
            except Exception as e:
                logger.warning(f"Could not ask why {item.item_id} was refused: {e}")
                continue
            if reason := thread.tombstones.get(item.item_id):
                await self.db.set_tombstone_reason(item.item_id, reason)
                logger.info(f"{item.item_id}: {reason}")

    async def _expand_recovered(self, category: str, seeds: list[dict[str, Any]]):
        """A recovered tweet arrives with its graph unexplored: while it was a tombstone it had no
        payload, so its quoted tweet, its parent and its own thread were never followed. Origins
        are left alone — how each tweet was found belongs to the run that found it."""
        cat = next(c for c in CATEGORIES if c.name == category)
        expander = TweetExpander(self.tw_client, page_delay=PAGE_DELAY, seed_origin=cat.seed_origin)
        tweets = await expander.expand(seeds)

        existing = await self.db.all_ids(PLATFORM)
        new_tweets = [t for t in tweets if t.id not in existing]
        logger.info(f"Recovered {category} expanded into {len(new_tweets)} further tweets")
        for tweet in new_tweets:
            await self.db.insert(tweet.to_item(category))

    async def _archive_category(self, category: Category, fetch_all: bool):
        logger.info(f"Archiving {category.name} (fetch_all={fetch_all})")
        delay = INITIAL_RETRY_DELAY

        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                await self._record_new_tweets(category, fetch_all)
                return
            except Exception as e:
                if _is_rate_limit(e) and attempt < FETCH_ATTEMPTS:
                    logger.warning(f"Rate limited (attempt {attempt}/{FETCH_ATTEMPTS}); waiting {delay:.0f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, MAX_RETRY_DELAY)
                    continue
                logger.error(f"Archiving {category.name} failed: {e}")
                if self.tg:
                    await self.tg.send_error_notification(
                        type(e).__name__, f"archive:{category.name}", traceback.format_exc()
                    )
                raise

    async def _record_new_tweets(self, category: Category, fetch_all: bool):
        known_ids = None if fetch_all else await self.db.ids_by_origin(PLATFORM, category.seed_origin)
        if known_ids is not None:
            logger.info(f"Cursor-based sync: {len(known_ids)} known {category.seed_origin} tweets")

        seeds = await self._fetch_seeds(category, known_ids)
        if seeds:
            await self._expand_and_record(category, seeds)
        else:
            logger.info(f"No new {category.name} from the timeline")

        await self._backfill_from_export(category)

    async def _expand_and_record(self, category: Category, seeds: list[dict[str, Any]]):
        expander = TweetExpander(self.tw_client, page_delay=PAGE_DELAY, seed_origin=category.seed_origin)
        tweets = await expander.expand(seeds)
        logger.info(f"Expanded {len(seeds)} {category.name} into {len(tweets)} tweets")

        existing = await self.db.all_ids(PLATFORM)

        upgraded = 0
        for tweet in tweets:
            if tweet.origin == category.seed_origin and tweet.id in existing:
                if await self.db.upgrade_origin(tweet.id, category.seed_origin):
                    upgraded += 1
        if upgraded:
            logger.info(f"Upgraded origin to '{category.seed_origin}' for {upgraded} previously-discovered tweets")

        new_tweets = [t for t in tweets if t.id not in existing]
        logger.info(f"Recording {len(new_tweets)} new tweets ({len(tweets) - len(new_tweets)} already archived)")
        for tweet in new_tweets:
            await self.db.insert(tweet.to_item(category.name))

    async def _fetch_seeds(self, category: Category, known_ids: set[str] | None) -> list[dict[str, Any]]:
        fetch = {"likes": self.tw_client.get_all_likes, "bookmarks": self.tw_client.get_all_bookmarks}[category.name]
        result = await fetch(limit=0, page_delay=PAGE_DELAY, known_ids=known_ids)
        if not result["success"]:
            raise RuntimeError(f"Fetching {category.name} failed: {result['error']}")
        return result["tweets"]

    async def _backfill_from_export(self, category: Category):
        """Backfill likes from the official export in resumable chunks: like.js holds
        every like id ever, past the Likes-timeline cap. Twitter rate-limits hard, so a
        full pass is impossible in one go — each chunk is expanded and committed before
        the next, so a stopped run resumes from the database. No bookmarks in exports."""
        if category.name != "likes" or not config.TWITTER_EXPORT_PATH:
            return
        path = Path(config.TWITTER_EXPORT_PATH)
        if not path.exists():
            logger.warning(f"TWITTER_EXPORT_PATH does not exist, skipping export ingest: {path}")
            return

        export_seeds = parse_like_export(path)
        while True:
            existing = await self.db.all_ids(PLATFORM)
            queued = [seed for seed in export_seeds if seed["id"] not in existing]
            if not queued:
                break
            chunk = queued[: config.TWITTER_EXPORT_BATCH]
            logger.info(f"Export backfill: expanding {len(chunk)} likes ({len(queued) - len(chunk)} still queued)")
            await self._expand_and_record(category, chunk)


def _is_rate_limit(error: Exception) -> bool:
    message = str(error).lower()
    return "429" in message or "rate" in message
