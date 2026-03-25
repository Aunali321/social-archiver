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
from twitter_archiver.fetchers.bookmarks import BookmarksFetcher
from twitter_archiver.fetchers.likes import LikesFetcher
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

        self.bookmarks_fetcher = BookmarksFetcher(tw_client)
        self.likes_fetcher = LikesFetcher(tw_client)

    async def process_category(self, category: str, fetch_all: bool = False):
        logger.info(f"Processing category: {category} (fetch_all={fetch_all})")

        max_retries = 5
        retry_delay = 180

        for attempt in range(1, max_retries + 1):
            try:
                if fetch_all:
                    tweet_list = await self._fetch_tweets(category, fetch_all=True)
                    await self._process_tweet_list(tweet_list, category)
                else:
                    await self._process_with_smart_pagination(category)
                return

            except Exception as e:
                error_msg = traceback.format_exc()

                # Check if it's a rate limit error
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "rate" in error_str

                if is_rate_limit and attempt < max_retries:
                    logger.warning(
                        f"Rate limit hit for {category} (attempt {attempt}/{max_retries}). "
                        f"Waiting {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 1800)
                    continue

                logger.error(f"Error processing {category}: {e}\n{error_msg}")
                await self.tg_client.send_error_notification(
                    error_type=type(e).__name__,
                    context=f"process_category:{category}",
                    traceback=error_msg,
                )
                raise

    async def _process_with_smart_pagination(self, category: str):
        tweet_list = await self._fetch_tweets(category, fetch_all=False)

        if not tweet_list:
            logger.info(f"{category}: No items found")
            return

        logger.info(f"{category}: Fetched {len(tweet_list)} items")

        new_tweets = []
        for tweet in tweet_list:
            if not await self.db.is_processed(tweet.id):
                new_tweets.append(tweet)

        logger.info(f"{category}: Found {len(new_tweets)}/{len(tweet_list)} new items")

        for tweet in new_tweets:
            await self._process_single_tweet(tweet, category)

        logger.info(f"{category}: Processed {len(new_tweets)} new items")

    async def _process_tweet_list(
        self, tweet_list: List[SimpleTweet], category: str
    ):
        logger.info(f"Fetched {len(tweet_list)} items from {category}")

        new_tweets = []
        for tweet in tweet_list:
            if not await self.db.is_processed(tweet.id):
                new_tweets.append(tweet)

        logger.info(f"Found {len(new_tweets)} new items in {category}")

        for tweet in new_tweets:
            await self._process_single_tweet(tweet, category)

    async def _process_single_tweet(self, tweet: SimpleTweet, category: str):
        paths = []
        try:
            await self.db.insert_tweet(
                tweet_id=tweet.id,
                category=category,
                author_username=tweet.author_username,
                author_id=tweet.author_id,
                tweet_text=tweet.text,
                post_url=tweet.post_url,
                has_media=tweet.has_media,
                media_count=len(tweet.media),
                created_at=tweet.created_at,
                status="pending",
            )

            # Download media if present
            if tweet.has_media:
                paths = await self.downloader.download_tweet_media(tweet, category)
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
            )

            chat_id = self._get_chat_id(category)

            # Upload to Telegram
            if paths:
                # Parallel upload and embedding generation
                upload_task = self.tg_client.send_media(
                    chat_id, paths, caption, has_video=tweet.has_video
                )

                embedding_task = None
                if config.EMBEDDING_ENABLED and self.embedding_processor:
                    embedding_task = self.embedding_processor.process_tweet(
                        tweet, category, paths
                    )

                if embedding_task:
                    results = await asyncio.gather(
                        upload_task, embedding_task, return_exceptions=True
                    )
                    message_ids = results[0]
                    embedding_success = results[1]

                    if isinstance(message_ids, Exception):
                        raise message_ids

                    if isinstance(embedding_success, Exception):
                        logger.error(
                            f"Embedding failed for {tweet.id}: {embedding_success}"
                        )
                        await self.db.mark_embedded(
                            tweet.id, False, str(embedding_success)
                        )
                    elif isinstance(embedding_success, tuple):
                        success, vlm_description = embedding_success
                        if success:
                            await self.db.mark_embedded(
                                tweet.id, True, vlm_description=vlm_description
                            )
                        else:
                            await self.db.mark_embedded(
                                tweet.id, False, "Embedding generation returned False"
                            )
                else:
                    message_ids = await upload_task
            else:
                # Text-only tweet
                message_ids = await self.tg_client.send_text(chat_id, caption)

                # Embedding for text-only tweets
                if config.EMBEDDING_ENABLED and self.embedding_processor:
                    try:
                        result = await self.embedding_processor.process_tweet(
                            tweet, category, []
                        )
                        if isinstance(result, tuple):
                            success, vlm_description = result
                            await self.db.mark_embedded(
                                tweet.id, success, vlm_description=vlm_description
                            )
                    except Exception as e:
                        logger.error(f"Embedding failed for {tweet.id}: {e}")
                        await self.db.mark_embedded(tweet.id, False, str(e))

            await self.db.mark_uploaded(tweet.id, message_ids)
            logger.info(f"Successfully processed {tweet.id} to {category}")

            if config.CLEANUP_DOWNLOADS and paths:
                self._cleanup_downloads(paths)

        except RetryAfter as e:
            error_msg = f"Telegram flood control: retry after {e.retry_after}s"
            logger.warning(f"Flood control hit for {tweet.id}: {error_msg}")
            await self.db.update_status(tweet.id, "pending", error_msg)
            if config.CLEANUP_DOWNLOADS and paths:
                self._cleanup_downloads(paths)
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"Failed to process {tweet.id}: {e}")
            await self.db.update_status(tweet.id, "failed", str(e))
            await self.tg_client.send_error_notification(
                error_type=type(e).__name__,
                context=f"process_tweet:{tweet.id}",
                traceback=error_msg,
            )

    def _cleanup_downloads(self, paths: List[Path]):
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Cleaned up: {path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {path}: {e}")

    async def _fetch_tweets(
        self, category: str, fetch_all: bool
    ) -> List[SimpleTweet]:
        amount = 0 if fetch_all else config.FETCH_BATCH_SIZE

        if category == "bookmarks":
            return await self.bookmarks_fetcher.fetch_bookmarks(amount)
        elif category == "likes":
            return await self.likes_fetcher.fetch_likes(amount)
        else:
            raise ValueError(f"Unknown category: {category}")

    def _get_chat_id(self, category: str) -> int:
        chat_map = {
            "bookmarks": config.TELEGRAM_CHAT_BOOKMARKS,
            "likes": config.TELEGRAM_CHAT_LIKES,
        }
        chat_id = chat_map.get(category, 0)
        if not chat_id:
            raise ValueError(
                f"No Telegram chat ID configured for category: {category}"
            )
        return chat_id
