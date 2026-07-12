import logging
import asyncio
import traceback
from typing import List, Optional
from pathlib import Path
from telegram.error import RetryAfter
from twitter_archiver import config
from twitter_archiver.twitter_client import TwitterClient
from twitter_archiver.telegram_client import TelegramClient
from twitter_archiver.database import Database
from twitter_archiver.downloader import MediaDownloader
from twitter_archiver.expander import TweetExpander
from twitter_archiver.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)


class Processor:
    def __init__(
        self,
        tw_client: TwitterClient,
        tg_client: TelegramClient,
        db: Database,
        downloader: MediaDownloader,
        embedding_processor: Optional["EmbeddingProcessor"] = None,
    ):
        self.tw_client = tw_client
        self.tg_client = tg_client
        self.db = db
        self.downloader = downloader
        self.embedding_processor = embedding_processor

    async def process_likes(self, fetch_all: bool = False):
        """
        Main processing pipeline for likes:
        1. Fetch all liked tweets
        2. Expand recursively (threads, parents, quotes, liked replies)
        3. Deduplicate against DB
        4. Download, upload, embed each new tweet
        """
        logger.info(f"Processing likes (fetch_all={fetch_all})")

        max_retries = 5
        retry_delay = 180

        for attempt in range(1, max_retries + 1):
            try:
                # Step 1: Fetch liked tweets
                # For sync mode, pass known IDs so we stop at the first known tweet
                known_ids = None
                if not fetch_all:
                    known_ids = await self.db.get_liked_tweet_ids()
                    logger.info(f"Cursor-based sync: {len(known_ids)} known liked tweets in DB")

                raw_likes = await self._fetch_raw_likes(
                    amount=0, known_ids=known_ids
                )
                if not raw_likes:
                    logger.info("No likes found")
                    return

                logger.info(f"Fetched {len(raw_likes)} liked tweets")

                # Step 2: Expand recursively
                expander = TweetExpander(self.tw_client, page_delay=1.5)
                expanded_tweets = await expander.expand_likes(raw_likes)

                logger.info(
                    f"Expanded to {len(expanded_tweets)} total tweets "
                    f"(from {len(raw_likes)} likes)"
                )

                # Step 3: Deduplicate against DB
                existing_ids = await self.db.get_all_tweet_ids()
                new_tweets = [t for t in expanded_tweets if t.id not in existing_ids]

                logger.info(
                    f"Found {len(new_tweets)} new tweets "
                    f"({len(expanded_tweets) - len(new_tweets)} already in DB)"
                )

                if not new_tweets:
                    return

                # Step 4: Process each tweet
                # Sort so that liked tweets come first, then by creation time
                new_tweets.sort(key=lambda t: (
                    0 if t.origin == "liked" else 1,
                    t.created_at or datetime_min(),
                ))

                # Phase A: DB insert, download, telegram upload for each tweet
                media_paths_map = {}  # tweet_id -> list of Paths
                for tweet in new_tweets:
                    paths = await self._process_single_tweet(tweet)
                    if paths:
                        media_paths_map[tweet.id] = paths

                # Phase B: One VLM call for the whole expanded group
                if config.EMBEDDING_ENABLED and self.embedding_processor:
                    try:
                        sorted_tweets = sorted(
                            new_tweets,
                            key=lambda t: t.created_at or datetime_min(),
                        )
                        success, descriptions = await self.embedding_processor.process_expanded_group(
                            sorted_tweets, media_paths_map
                        )
                        if success:
                            for tweet in new_tweets:
                                desc = descriptions.get(tweet.id) if descriptions else None
                                await self.db.mark_embedded(
                                    tweet.id, True, vlm_description=desc
                                )
                        else:
                            for tweet in new_tweets:
                                await self.db.mark_embedded(
                                    tweet.id, False, "Group VLM call failed"
                                )
                    except Exception as e:
                        logger.error(f"Batch embedding failed: {e}")
                        for tweet in new_tweets:
                            await self.db.mark_embedded(tweet.id, False, str(e))

                # Phase C: Cleanup downloaded files if configured
                if config.CLEANUP_DOWNLOADS:
                    for paths in media_paths_map.values():
                        self._cleanup_downloads(paths)

                logger.info(f"Processed {len(new_tweets)} new tweets")
                return

            except Exception as e:
                error_msg = traceback.format_exc()

                # Check if it's a rate limit error
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "rate" in error_str

                if is_rate_limit and attempt < max_retries:
                    logger.warning(
                        f"Rate limit hit (attempt {attempt}/{max_retries}). "
                        f"Waiting {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 1800)
                    continue

                logger.error(f"Error processing likes: {e}\n{error_msg}")
                await self.tg_client.send_error_notification(
                    error_type=type(e).__name__,
                    context="process_likes",
                    traceback=error_msg,
                )
                raise

    async def _fetch_raw_likes(
        self, amount: int = 0, known_ids: set = None
    ) -> List[dict]:
        """Fetch raw liked tweet dicts from the API.
        If known_ids is provided, stops fetching when a known tweet is hit."""
        result = await self.tw_client.get_all_likes(
            limit=amount, page_delay=1.5, known_ids=known_ids
        )

        if not result.get("success"):
            error = result.get("error", "Unknown error")
            raise RuntimeError(f"Failed to fetch likes: {error}")

        return result.get("tweets", [])

    async def _process_single_tweet(self, tweet: SimpleTweet) -> Optional[List[Path]]:
        """Process a single tweet: insert in DB, download media, upload to Telegram.

        Returns list of downloaded media paths (for batch embedding later).
        """
        paths = []
        try:
            # Insert into DB with full linking
            await self.db.insert_tweet(**tweet.to_db_dict())

            # Download media if present
            if tweet.has_media:
                paths = await self.downloader.download_tweet_media(tweet)
                if paths:
                    await self.db.mark_downloaded(tweet.id, [str(p) for p in paths])

            # Format caption
            caption = self.tg_client.format_caption(
                tweet.text,
                tweet.author_username,
                tweet.id,
                tweet.created_at,
                like_count=tweet.like_count,
                retweet_count=tweet.retweet_count,
                origin=tweet.origin,
            )

            chat_id = config.TELEGRAM_CHAT_LIKES
            if not chat_id:
                raise ValueError("TELEGRAM_CHAT_LIKES is not set")

            # Upload to Telegram
            if paths:
                message_ids = await self.tg_client.send_media(
                    chat_id, paths, caption, has_video=tweet.has_video
                )
            else:
                message_ids = await self.tg_client.send_text(chat_id, caption)

            await self.db.mark_uploaded(tweet.id, message_ids)
            logger.info(
                f"Processed {tweet.id} (@{tweet.author_username}, origin={tweet.origin})"
            )

            # Don't cleanup yet — paths needed for batch embedding
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
                error_type=type(e).__name__,
                context=f"process_tweet:{tweet.id}",
                traceback=error_msg,
            )
            return None

    def _cleanup_downloads(self, paths: List[Path]):
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Cleaned up: {path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {path}: {e}")


def datetime_min():
    from datetime import datetime, timezone
    return datetime(1970, 1, 1, tzinfo=timezone.utc)
