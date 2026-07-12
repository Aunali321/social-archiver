import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from telegram.error import RetryAfter

from social_archiver.core.database import Database
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.downloader import MediaDownloader
from social_archiver.platforms.twitter.expander import TweetExpander
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)

PLATFORM = "twitter"

SEED_ORIGINS = ("liked", "bookmarked")

_ORIGIN_LABELS = {
    "thread": "thread",
    "parent": "parent",
    "quoted": "quoted",
    "linked": "linked",
    "liked_reply": "liked reply",
    "retweet": "retweet",
}


def _datetime_min() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def format_caption(tweet: SimpleTweet) -> str:
    parts = []
    if tweet.text:
        parts.append(tweet.text)

    if tweet.origin and tweet.origin not in SEED_ORIGINS:
        parts.append(f"\n[{_ORIGIN_LABELS.get(tweet.origin, tweet.origin)}]")

    parts.append(f"@{tweet.author_username}")

    stats = []
    if tweet.like_count is not None:
        stats.append(f"{tweet.like_count} likes")
    if tweet.retweet_count is not None:
        stats.append(f"{tweet.retweet_count} RTs")
    if stats:
        parts.append(" | ".join(stats))

    parts.append(tweet.post_url)
    if tweet.created_at:
        parts.append(tweet.created_at.strftime("%Y-%m-%d %H:%M:%S"))

    return "\n".join(parts)


class Processor:
    def __init__(
        self,
        tw_client: TwitterClient,
        tg_client: TelegramClient,
        db: Database,
        downloader: MediaDownloader,
        embedding_processor=None,
    ):
        self.tw_client = tw_client
        self.tg_client = tg_client
        self.db = db
        self.downloader = downloader
        self.embedding_processor = embedding_processor

    async def process_all(self, fetch_all: bool = False):
        """Run every category whose Telegram channel is configured."""
        if config.TELEGRAM_CHAT_LIKES:
            await self.process_likes(fetch_all=fetch_all)
        if config.TELEGRAM_CHAT_BOOKMARKS:
            await self.process_bookmarks(fetch_all=fetch_all)

    async def process_likes(self, fetch_all: bool = False):
        await self._process_category(
            category="likes",
            seed_origin="liked",
            chat_id=config.TELEGRAM_CHAT_LIKES,
            downloads_dir=config.DOWNLOADS_LIKES,
            fetch_fn=self._fetch_raw_likes,
            fetch_all=fetch_all,
        )

    async def process_bookmarks(self, fetch_all: bool = False):
        await self._process_category(
            category="bookmarks",
            seed_origin="bookmarked",
            chat_id=config.TELEGRAM_CHAT_BOOKMARKS,
            downloads_dir=config.DOWNLOADS_BOOKMARKS,
            fetch_fn=self._fetch_raw_bookmarks,
            fetch_all=fetch_all,
        )

    async def _process_category(
        self,
        category: str,
        seed_origin: str,
        chat_id: int,
        downloads_dir: Path,
        fetch_fn,
        fetch_all: bool,
    ):
        """1. Fetch seed tweets. 2. Expand recursively. 3. Dedup against DB. 4. Archive each new tweet."""
        logger.info(f"Processing {category} (fetch_all={fetch_all})")
        if not chat_id:
            raise ValueError(f"Telegram chat for {category} is not set")

        max_retries = 5
        retry_delay = 180

        for attempt in range(1, max_retries + 1):
            try:
                known_ids = None if fetch_all else await self.db.get_item_ids_by_origin(PLATFORM, seed_origin)
                if known_ids is not None:
                    logger.info(f"Cursor-based sync: {len(known_ids)} known {seed_origin} tweets in DB")

                raw_seeds = await fetch_fn(known_ids=known_ids)
                if not raw_seeds:
                    logger.info(f"No {category} found")
                    return

                logger.info(f"Fetched {len(raw_seeds)} {category}")

                expander = TweetExpander(self.tw_client, page_delay=1.5, seed_origin=seed_origin)
                expanded_tweets = await expander.expand(raw_seeds)
                logger.info(f"Expanded to {len(expanded_tweets)} total tweets (from {len(raw_seeds)} {category})")

                existing_ids = await self.db.get_all_item_ids(PLATFORM)

                upgraded = 0
                for tweet in expanded_tweets:
                    if tweet.origin == seed_origin and tweet.id in existing_ids:
                        if await self.db.upgrade_origin(tweet.id, seed_origin):
                            upgraded += 1
                if upgraded:
                    logger.info(f"Upgraded origin to '{seed_origin}' for {upgraded} previously-discovered tweets")

                new_tweets = [t for t in expanded_tweets if t.id not in existing_ids]
                logger.info(f"Found {len(new_tweets)} new tweets ({len(expanded_tweets) - len(new_tweets)} already in DB)")

                if not new_tweets:
                    return

                new_tweets.sort(key=lambda t: (0 if t.origin == seed_origin else 1, t.created_at or _datetime_min()))

                media_paths_map: dict[str, list[Path]] = {}
                for tweet in new_tweets:
                    if paths := await self._process_single_tweet(tweet, chat_id, category, downloads_dir):
                        media_paths_map[tweet.id] = paths

                embeddable = [t for t in new_tweets if not t.is_tombstone]
                if config.EMBEDDING_ENABLED and self.embedding_processor and embeddable:
                    await self._embed_group(embeddable, media_paths_map, category)

                if config.CLEANUP_DOWNLOADS:
                    for paths in media_paths_map.values():
                        self._cleanup_downloads(paths)

                logger.info(f"Processed {len(new_tweets)} new tweets")
                return

            except Exception as e:
                error_msg = traceback.format_exc()
                is_rate_limit = "429" in str(e).lower() or "rate" in str(e).lower()

                if is_rate_limit and attempt < max_retries:
                    logger.warning(f"Rate limit hit (attempt {attempt}/{max_retries}). Waiting {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 1800)
                    continue

                logger.error(f"Error processing {category}: {e}\n{error_msg}")
                await self.tg_client.send_error_notification(
                    error_type=type(e).__name__, context=f"process_{category}", traceback=error_msg
                )
                raise

    async def _embed_group(self, new_tweets: list[SimpleTweet], media_paths_map: dict[str, list[Path]], category: str):
        try:
            sorted_tweets = sorted(new_tweets, key=lambda t: t.created_at or _datetime_min())
            success, descriptions = await self.embedding_processor.process_expanded_group(
                sorted_tweets, media_paths_map, category=category
            )
            for tweet in new_tweets:
                if success:
                    desc = descriptions.get(tweet.id) if descriptions else None
                    await self.db.mark_embedded(tweet.id, True, vlm_description=desc)
                else:
                    await self.db.mark_embedded(tweet.id, False, "Group VLM call failed")
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            for tweet in new_tweets:
                await self.db.mark_embedded(tweet.id, False, str(e))

    async def _fetch_raw_likes(self, known_ids: set | None = None) -> list[dict]:
        result = await self.tw_client.get_all_likes(limit=0, page_delay=1.5, known_ids=known_ids)
        if not result.get("success"):
            raise RuntimeError(f"Failed to fetch likes: {result.get('error', 'Unknown error')}")
        return result.get("tweets", [])

    async def _fetch_raw_bookmarks(self, known_ids: set | None = None) -> list[dict]:
        result = await self.tw_client.get_all_bookmarks(limit=0, page_delay=1.5, known_ids=known_ids)
        if not result.get("success"):
            raise RuntimeError(f"Failed to fetch bookmarks: {result.get('error', 'Unknown error')}")
        return result.get("tweets", [])

    async def _process_single_tweet(
        self, tweet: SimpleTweet, chat_id: int, category: str, downloads_dir: Path
    ) -> list[Path] | None:
        """Insert in DB, download media, upload to Telegram. Returns downloaded
        paths (kept for batch embedding, not cleaned up yet)."""
        paths: list[Path] = []
        try:
            await self.db.insert_item(**tweet.to_db_dict(category))

            if tweet.is_tombstone:
                logger.info(f"Recorded tombstone {tweet.id} (origin={tweet.origin}): {tweet.text}")
                return None

            if tweet.has_media:
                paths = await self.downloader.download_tweet_media(tweet, folder=downloads_dir)
                if paths:
                    await self.db.mark_downloaded(tweet.id, [str(p) for p in paths])

            caption = format_caption(tweet)
            message_ids = (
                await self.tg_client.send_media(chat_id, paths, caption)
                if paths
                else await self.tg_client.send_text(chat_id, caption)
            )

            await self.db.mark_uploaded(tweet.id, message_ids)
            logger.info(f"Processed {tweet.id} (@{tweet.author_username}, origin={tweet.origin})")
            return paths or None

        except RetryAfter as e:
            error_msg = f"Telegram flood control: retry after {e.retry_after}s"
            logger.warning(f"Flood control hit for {tweet.id}: {error_msg}")
            await self.db.update_status(tweet.id, "pending", error_msg)
            return None
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"Failed to process {tweet.id}: {e}")
            await self.db.update_status(tweet.id, "failed", str(e))
            await self.tg_client.send_error_notification(
                error_type=type(e).__name__, context=f"process_tweet:{tweet.id}", traceback=error_msg
            )
            return None

    def _cleanup_downloads(self, paths: list[Path]):
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Cleaned up: {path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {path}: {e}")
