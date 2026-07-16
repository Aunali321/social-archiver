import asyncio
import logging
import traceback

from instagrapi.exceptions import FeedbackRequired

from social_archiver.core.database import Database
from social_archiver.core.jobs import ensure_media
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.likes import LikesFetcher
from social_archiver.platforms.instagram.fetchers.saved import SavedFetcher
from social_archiver.platforms.instagram.fetchers.shared import SharedFetcher
from social_archiver.platforms.instagram.port import PLATFORM, InstagramPort
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

FETCH_ATTEMPTS = 10
INITIAL_RETRY_DELAY = 180.0
MAX_RETRY_DELAY = 1800.0


class ArchiveJob:
    """Fetches liked/saved/DM-shared posts, records them, and downloads media
    to disk. Uploading and embedding are separate jobs; rows are committed
    before any download starts, so an interrupted run resumes where it stopped."""

    def __init__(self, ig_client: InstagramClient, db: Database, port: InstagramPort, tg: TelegramClient):
        self.db = db
        self.port = port
        self.tg = tg
        self.likes = LikesFetcher(ig_client)
        self.saved = SavedFetcher(ig_client)
        self.shared = SharedFetcher(ig_client)

    async def run(self, fetch_all: bool = False, category: str | None = None):
        for name, chat_id in self.port.chats.items():
            if chat_id and category in (None, name):
                await self._archive_category(name, fetch_all)
        await self._download_pending()

    async def _archive_category(self, category: str, fetch_all: bool):
        logger.info(f"Archiving {category} (fetch_all={fetch_all})")
        delay = INITIAL_RETRY_DELAY

        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                await self._record_new_media(category, fetch_all)
                return
            except FeedbackRequired as e:
                if attempt < FETCH_ATTEMPTS:
                    logger.warning(f"Rate limited (attempt {attempt}/{FETCH_ATTEMPTS}); waiting {delay:.0f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, MAX_RETRY_DELAY)
                    continue
                await self._notify_and_raise(e, category)
            except Exception as e:
                await self._notify_and_raise(e, category)

    async def _notify_and_raise(self, error: Exception, category: str):
        logger.error(f"Archiving {category} failed: {error}")
        await self.tg.send_error_notification(type(error).__name__, f"archive:{category}", traceback.format_exc())
        raise error

    async def _record_new_media(self, category: str, fetch_all: bool):
        media_list = self._fetch(category, fetch_all)
        logger.info(f"Fetched {len(media_list)} {category} items")

        existing = await self.db.all_ids(PLATFORM)
        new_media = [media for media in media_list if media.pk not in existing]
        logger.info(f"Recording {len(new_media)} new items ({len(media_list) - len(new_media)} already archived)")
        for media in new_media:
            await self.db.insert(media.to_item(category))

    def _fetch(self, category: str, fetch_all: bool) -> list[SimpleMedia]:
        amount = 0 if fetch_all else config.FETCH_BATCH_SIZE
        match category:
            case "likes":
                return self.likes.fetch_liked_media(amount)
            case "saved":
                return self.saved.fetch_saved_media(amount)
            case "shared":
                return self.shared.fetch_shared_media(config.INSTAGRAM_DM_USERNAME, amount)
            case _:
                raise ValueError(f"Unknown category: {category}")

    async def _download_pending(self):
        pending = await self.db.pending_archive(PLATFORM)
        if not pending:
            return

        logger.info(f"Downloading media for {len(pending)} items")
        for item in pending:
            try:
                await ensure_media(self.db, self.port, item)
            except Exception as e:
                logger.error(f"Download failed for {item.item_id}: {e}")
                await self.db.mark_archive_failed(item.item_id, str(e))
