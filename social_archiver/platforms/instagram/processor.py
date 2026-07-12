import asyncio
import logging
import traceback
from pathlib import Path

from instagrapi.exceptions import FeedbackRequired
from telegram.error import RetryAfter

from social_archiver.core.database import Database
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.downloader import AnyMedia, MediaDownloader
from social_archiver.platforms.instagram.fetchers.likes import LikesFetcher
from social_archiver.platforms.instagram.fetchers.saved import SavedFetcher
from social_archiver.platforms.instagram.fetchers.shared import SharedFetcher
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

PLATFORM = "instagram"


def format_caption(
    media: AnyMedia,
    collection_name: str | None = None,
    shared_by_username: str | None = None,
) -> str:
    parts = []
    if media.caption_text:
        parts.append(media.caption_text)
    if collection_name:
        parts.append(f"\n📁 {collection_name}")
    parts.append(f"👤 @{media.user.username}")
    if shared_by_username:
        parts.append(f"📤 Shared by @{shared_by_username}")
    parts.append(f"🔗 https://instagram.com/p/{media.code}")
    parts.append(f"📅 {media.taken_at.strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(parts)


class Processor:
    def __init__(
        self,
        ig_client: InstagramClient,
        tg_client: TelegramClient,
        db: Database,
        downloader: MediaDownloader,
        embedding_processor=None,
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

        max_retries = 10
        retry_delay = 180

        for attempt in range(1, max_retries + 1):
            try:
                media_list = self._fetch_media(category, fetch_all)
                await self._process_media_list(media_list, category)
                return

            except FeedbackRequired as e:
                if attempt >= max_retries:
                    error_msg = traceback.format_exc()
                    logger.error(f"Failed to process {category} after {max_retries} attempts due to FeedbackRequired.")
                    await self.tg_client.send_error_notification(
                        error_type=type(e).__name__,
                        context=f"process_category:{category} (max_retries_reached)",
                        traceback=error_msg,
                    )
                    raise
                logger.warning(f"Instagram rate limit hit for {category} (attempt {attempt}/{max_retries}). Waiting {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 1800)

            except Exception as e:
                error_msg = traceback.format_exc()
                logger.error(f"Error processing {category}: {e}\n{error_msg}")
                await self.tg_client.send_error_notification(
                    error_type=type(e).__name__, context=f"process_category:{category}", traceback=error_msg
                )
                raise

    async def _process_media_list(self, media_list: list[AnyMedia], category: str):
        logger.info(f"Fetched {len(media_list)} items from {category}")

        new_media = [m for m in media_list if not await self.db.is_processed(PLATFORM, str(m.pk))]
        logger.info(f"Found {len(new_media)} new items in {category}")

        for media in new_media:
            await self._process_single_media(media, category)

    async def _process_single_media(self, media: AnyMedia, category: str):
        paths: list[Path] = []
        try:
            metadata = {}
            if category == "saved" and isinstance(media, SimpleMedia) and media.collection_name:
                metadata["collection_name"] = media.collection_name
            if category == "shared":
                shared_by = media.shared_by_username if isinstance(media, SimpleMedia) else None
                if not shared_by:
                    shared_by = self.shared_fetcher.get_sender_username(str(media.pk))
                if shared_by:
                    metadata["shared_by_username"] = shared_by
            if isinstance(media, SimpleMedia) and media.product_type:
                metadata["product_type"] = media.product_type

            media_types, media_count = self._describe_media(media)

            await self.db.insert_item(
                item_id=str(media.pk),
                platform=PLATFORM,
                category=category,
                author_username=media.user.username,
                author_id=str(media.user.pk),
                text=media.caption_text,
                post_url=f"https://instagram.com/p/{media.code}",
                created_at=media.taken_at,
                has_media=True,
                media_count=media_count,
                media_types=media_types,
                metadata=metadata or None,
            )

            paths = await self.downloader.download_media(media, category)

            if not paths:
                logger.info(f"Skipped media {media.pk} (IGTV)")
                await self.db.update_status(str(media.pk), "skipped", "IGTV content excluded")
                return

            await self.db.mark_downloaded(str(media.pk), [str(p) for p in paths])

            caption = format_caption(
                media,
                collection_name=metadata.get("collection_name"),
                shared_by_username=metadata.get("shared_by_username"),
            )
            chat_id = self._get_chat_id(category)

            upload_task = self.tg_client.send_media(chat_id, paths, caption)
            embedding_task = (
                self.embedding_processor.process_media(str(media.pk), media, category, paths)
                if config.EMBEDDING_ENABLED and self.embedding_processor
                else None
            )

            if embedding_task:
                message_ids, embedding_result = await asyncio.gather(upload_task, embedding_task, return_exceptions=True)
                if isinstance(message_ids, Exception):
                    raise message_ids
                await self._record_embedding_result(str(media.pk), embedding_result)
            else:
                message_ids = await upload_task

            await self.db.mark_uploaded(str(media.pk), message_ids)
            logger.info(f"Successfully processed {media.pk} to {category}")

            if config.CLEANUP_DOWNLOADS:
                self._cleanup_downloads(paths)

        except RetryAfter as e:
            error_msg = f"Telegram flood control: retry after {e.retry_after}s"
            logger.warning(f"Flood control hit for {media.pk}: {error_msg}")
            await self.db.update_status(str(media.pk), "pending", error_msg)
            if config.CLEANUP_DOWNLOADS:
                self._cleanup_downloads(paths)
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"Failed to process {media.pk}: {e}")
            await self.db.update_status(str(media.pk), "failed", str(e))
            await self.tg_client.send_error_notification(
                error_type=type(e).__name__, context=f"process_media:{media.pk}", traceback=error_msg
            )

    async def _record_embedding_result(self, item_id: str, result) -> None:
        if isinstance(result, Exception):
            logger.error(f"Embedding failed for {item_id}: {result}")
            await self.db.mark_embedded(item_id, False, str(result))
            await self.tg_client.send_error_notification(
                error_type=type(result).__name__,
                context=f"embedding:{item_id}",
                traceback="".join(traceback.format_exception(type(result), result, result.__traceback__)),
            )
        elif isinstance(result, tuple):
            success, vlm_description = result
            if success:
                await self.db.mark_embedded(item_id, True, vlm_description=vlm_description)
            else:
                await self.db.mark_embedded(item_id, False, "Embedding generation returned False")
        else:
            await self.db.mark_embedded(item_id, False, "Unexpected embedding result type")

    def _describe_media(self, media: AnyMedia) -> tuple[list[str], int]:
        """Map instagrapi's media_type (1=photo, 2=video, 8=album) to (media_types, media_count)."""
        if media.media_type == 8:
            kinds = {"photo" if r.media_type == 1 else "video" for r in media.resources}
            return sorted(kinds), len(media.resources)
        return (["photo"] if media.media_type == 1 else ["video"]), 1

    def _cleanup_downloads(self, paths: list[Path]):
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Cleaned up: {path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {path}: {e}")

    def _fetch_media(self, category: str, fetch_all: bool) -> list[AnyMedia]:
        amount = 0 if fetch_all else config.FETCH_BATCH_SIZE

        if category == "likes":
            return self.likes_fetcher.fetch_liked_media(amount)
        if category == "saved":
            return self.saved_fetcher.fetch_saved_media(amount)
        if category == "shared":
            return self.shared_fetcher.fetch_shared_media(config.INSTAGRAM_DM_USERNAME, amount)
        raise ValueError(f"Unknown category: {category}")

    def _get_chat_id(self, category: str) -> int:
        return {
            "likes": config.TELEGRAM_CHAT_LIKES,
            "saved": config.TELEGRAM_CHAT_SAVED,
            "shared": config.TELEGRAM_CHAT_SHARED,
        }[category]
