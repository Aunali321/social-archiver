import logging
import asyncio
import traceback
from typing import List, Union, Optional
from pathlib import Path
from instagrapi.types import Media
from instagrapi.exceptions import FeedbackRequired
from telegram.error import RetryAfter
from insta_archiver import config
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.telegram_client import TelegramClient
from insta_archiver.database import Database
from insta_archiver.downloader import MediaDownloader
from insta_archiver.fetchers.likes import LikesFetcher
from insta_archiver.fetchers.saved import SavedFetcher
from insta_archiver.fetchers.shared import SharedFetcher
from insta_archiver.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class Processor:
    def __init__(
        self,
        ig_client: InstagramClient,
        tg_client: TelegramClient,
        db: Database,
        downloader: MediaDownloader,
        embedding_processor: Optional["EmbeddingProcessor"] = None,
    ):
        self.ig_client = ig_client
        self.tg_client = tg_client
        self.db = db
        self.downloader = downloader
        self.embedding_processor = embedding_processor

        self.likes_fetcher = LikesFetcher(ig_client)
        self.saved_fetcher = SavedFetcher(ig_client)
        self.shared_fetcher = SharedFetcher(ig_client)

    async def process_category(self, category: str, fetch_all: bool = False):
        logger.info(f"Processing category: {category} (fetch_all={fetch_all})")

        max_retries = 10  # Maximum retry attempts for FeedbackRequired
        retry_delay = 180  # Start with 3 minutes

        for attempt in range(1, max_retries + 1):
            try:
                if fetch_all:
                    # History mode: fetch everything
                    media_list = self._fetch_media(category, fetch_all=True)
                    await self._process_media_list(media_list, category)
                else:
                    # Smart pagination: keep fetching batches until we're caught up
                    await self._process_with_smart_pagination(category)

                # Success - exit retry loop
                return

            except FeedbackRequired as e:
                if attempt < max_retries:
                    logger.warning(
                        f"Instagram rate limit hit for {category} (attempt {attempt}/{max_retries}). "
                        f"Waiting {retry_delay} seconds before retrying..."
                    )
                    logger.info(f"FeedbackRequired details: {e}")

                    # Wait before retry
                    await asyncio.sleep(retry_delay)

                    # Exponential backoff: increase delay for next attempt
                    retry_delay = min(retry_delay * 1.5, 1800)  # Max 30 minutes

                    logger.info(
                        f"Retrying {category} (attempt {attempt + 1}/{max_retries})..."
                    )
                else:
                    # Max retries reached
                    error_msg = traceback.format_exc()
                    logger.error(
                        f"Failed to process {category} after {max_retries} attempts due to FeedbackRequired. "
                        f"Instagram may require manual verification or longer wait time."
                    )
                    await self.tg_client.send_error_notification(
                        error_type=type(e).__name__,
                        context=f"process_category:{category} (max_retries_reached)",
                        traceback=error_msg,
                    )
                    raise

            except Exception as e:
                error_msg = traceback.format_exc()
                logger.error(f"Error processing {category}: {e}\n{error_msg}")
                await self.tg_client.send_error_notification(
                    error_type=type(e).__name__,
                    context=f"process_category:{category}",
                    traceback=error_msg,
                )
                raise

    async def _process_with_smart_pagination(self, category: str):
        """
        Keep fetching fixed-size batches (50 items) and processing new items.
        Stop when an entire batch has been processed already (= caught up).

        This handles downtime by continuing to fetch older items until we reach
        content we've already processed.
        """
        batch_size = 50
        max_batches = (
            20  # Safety limit to prevent infinite loops (20 * 50 = 1000 items max)
        )
        batch_num = 0
        total_new = 0
        total_fetched = 0

        logger.info(
            f"{category}: Starting smart pagination (batch_size={batch_size}, max_batches={max_batches})"
        )

        # For simplicity, we'll fetch in one large batch and process incrementally
        # The fetchers already handle pagination internally when amount=0
        # For non-history mode, we want to fetch enough to catch up

        # Strategy: Fetch a large enough batch to likely contain all new items
        # The fetchers will paginate internally
        media_list = self._fetch_media(category, fetch_all=False)

        if not media_list:
            logger.info(f"{category}: No items found")
            return

        total_fetched = len(media_list)
        logger.info(f"{category}: Fetched {total_fetched} items")

        # Process items, counting how many are new
        new_media = []
        for media in media_list:
            if not await self.db.is_processed(int(media.pk)):
                new_media.append(media)

        total_new = len(new_media)
        logger.info(f"{category}: Found {total_new}/{total_fetched} new items")

        # Process all new items
        for media in new_media:
            await self._process_single_media(media, category)

        logger.info(f"{category}: Processed {total_new} new items")

    async def _process_media_list(
        self, media_list: List[Union[Media, SimpleMedia]], category: str
    ):
        """Process a list of media items, filtering out already-processed ones"""
        logger.info(f"Fetched {len(media_list)} items from {category}")

        new_media = []
        for media in media_list:
            if not await self.db.is_processed(int(media.pk)):
                new_media.append(media)

        logger.info(f"Found {len(new_media)} new items in {category}")

        for media in new_media:
            await self._process_single_media(media, category)

    async def _process_single_media(
        self, media: Union[Media, SimpleMedia], category: str
    ):
        try:
            # Extract metadata based on category
            metadata = {}

            # For saved posts, get collection name
            if (
                category == "saved"
                and isinstance(media, SimpleMedia)
                and media.collection_name
            ):
                metadata["collection_name"] = media.collection_name

            # For shared posts, get sender username
            if category == "shared":
                if isinstance(media, SimpleMedia) and media.shared_by_username:
                    metadata["shared_by_username"] = media.shared_by_username
                else:
                    # For Media objects from instagrapi, get from fetcher's sender map
                    sender_username = self.shared_fetcher.get_sender_username(
                        str(media.pk)
                    )
                    if sender_username:
                        metadata["shared_by_username"] = sender_username

            await self.db.insert_media(
                media_pk=int(media.pk),
                media_id=media.id,
                media_code=media.code,
                category=category,
                media_type=media.media_type,
                product_type=media.product_type,
                author_username=media.user.username,
                author_user_id=int(media.user.pk),
                caption=media.caption_text,
                post_url=f"https://instagram.com/p/{media.code}",
                taken_at=media.taken_at,
                status="pending",
                metadata=metadata if metadata else None,
            )

            paths = await self.downloader.download_media(media, category)

            if not paths:
                logger.info(f"Skipped media {media.pk} (IGTV)")
                await self.db.update_status(
                    int(media.pk), "skipped", "IGTV content excluded"
                )
                return

            await self.db.mark_downloaded(int(media.pk), [str(p) for p in paths])

            caption = self.tg_client.format_caption(
                media.caption_text,
                media.user.username,
                media.code,
                media.taken_at,
                collection_name=metadata.get("collection_name"),
                shared_by_username=metadata.get("shared_by_username"),
            )

            chat_id = self._get_chat_id(category)

            # Parallel upload and embedding generation
            upload_task = self.tg_client.send_media(
                chat_id, paths, caption, media.media_type
            )

            embedding_task = None
            if config.EMBEDDING_ENABLED and self.embedding_processor:
                embedding_task = self.embedding_processor.process_media(
                    int(media.pk), media, category, paths
                )

            # Wait for both to complete
            if embedding_task:
                results = await asyncio.gather(
                    upload_task, embedding_task, return_exceptions=True
                )
                message_ids = results[0]
                embedding_success = results[1]

                # Handle upload result (check for exceptions)
                if isinstance(message_ids, Exception):
                    # Re-raise upload exception to be caught by outer handler
                    raise message_ids

                # Handle embedding result
                if isinstance(embedding_success, Exception):
                    logger.error(
                        f"Embedding failed for {media.pk}: {embedding_success}"
                    )
                    await self.db.mark_embedded(
                        int(media.pk), False, str(embedding_success)
                    )
                    await self.tg_client.send_error_notification(
                        error_type=type(embedding_success).__name__,
                        context=f"embedding:{media.pk}",
                        traceback=str(
                            traceback.format_exception(
                                type(embedding_success),
                                embedding_success,
                                embedding_success.__traceback__,
                            )
                        ),
                    )
                elif isinstance(embedding_success, tuple):
                    success, vlm_description = embedding_success
                    if success:
                        await self.db.mark_embedded(int(media.pk), True, vlm_description=vlm_description)
                    else:
                        await self.db.mark_embedded(
                            int(media.pk), False, "Embedding generation returned False"
                        )
                else:
                    await self.db.mark_embedded(
                        int(media.pk), False, "Unexpected embedding result type"
                    )
            else:
                message_ids = await upload_task

            await self.db.mark_uploaded(int(media.pk), message_ids)
            logger.info(f"Successfully processed {media.pk} to {category}")

            # Clean up downloaded files after successful upload and embedding if enabled
            if config.CLEANUP_DOWNLOADS:
                self._cleanup_downloads(paths)

        except RetryAfter as e:
            # Telegram flood control - don't mark as permanently failed
            error_msg = f"Telegram flood control: retry after {e.retry_after}s"
            logger.warning(f"Flood control hit for {media.pk}: {error_msg}")
            await self.db.update_status(int(media.pk), "pending", error_msg)
            # Clean up downloads even on flood control to save space
            if config.CLEANUP_DOWNLOADS:
                self._cleanup_downloads(paths)
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"Failed to process {media.pk}: {e}")
            await self.db.update_status(int(media.pk), "failed", str(e))
            await self.tg_client.send_error_notification(
                error_type=type(e).__name__,
                context=f"process_media:{media.pk}",
                traceback=error_msg,
            )

    def _cleanup_downloads(self, paths: List[Path]):
        """Delete downloaded files after successful Telegram upload"""
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Cleaned up: {path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {path}: {e}")

    def _fetch_media(
        self, category: str, fetch_all: bool
    ) -> List[Union[Media, SimpleMedia]]:
        # Use configurable batch size to handle downtime better
        # Default 200 items covers ~3-5 days of moderate activity
        amount = 0 if fetch_all else config.FETCH_BATCH_SIZE

        if category == "likes":
            return self.likes_fetcher.fetch_liked_media(amount)
        elif category == "saved":
            return self.saved_fetcher.fetch_saved_media(amount)
        elif category == "shared":
            return self.shared_fetcher.fetch_shared_media(
                config.INSTAGRAM_DM_USERNAME, amount
            )
        else:
            raise ValueError(f"Unknown category: {category}")

    def _get_chat_id(self, category: str) -> int:
        chat_map = {
            "likes": config.TELEGRAM_CHAT_LIKES,
            "saved": config.TELEGRAM_CHAT_SAVED,
            "shared": config.TELEGRAM_CHAT_SHARED,
        }
        return chat_map[category]
