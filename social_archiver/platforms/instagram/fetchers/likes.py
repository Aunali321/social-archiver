import logging
from collections.abc import Iterator

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.page import FEED_PAGE_SIZE, MediaPage
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class LikesFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    def fetch_liked_media(self, amount: int = 0, start_cursor: str = "") -> Iterator[MediaPage]:
        """Yield one API page at a time so the caller commits each page before the
        next request goes out. Instagram blocks this endpoint mid-walk, and a
        buffered walk would lose every page it had already paid for."""
        logger.info(f"Fetching liked media (amount={amount if amount > 0 else 'all'})")

        max_id = start_cursor
        fetched = 0
        while amount == 0 or fetched < amount:
            page = self._fetch_liked_chunk(max_id)
            media = page.media[: amount - fetched] if amount > 0 else page.media
            fetched += len(media)
            if media:
                yield MediaPage(media, page.next_max_id, page.raw_count)
            if not page.has_more:
                break
            max_id = page.next_max_id

        logger.info(f"Fetched {fetched} liked media")

    def _fetch_liked_chunk(self, max_id: str = "") -> MediaPage:
        """Fetch a chunk of liked media, tolerating fields that break instagrapi's strict extractor."""
        params = {"include_igtv_preview": "false", "count": str(FEED_PAGE_SIZE)}
        if max_id:
            params["max_id"] = max_id

        result = self.ig_client.private_request("feed/liked/", params)
        raw_items = result.get("items", [])

        items = []
        for item in raw_items:
            media_data = item.get("media", item)
            try:
                items.append(SimpleMedia.from_dict(media_data))
            except Exception as e:
                logger.warning(f"Failed to extract media {media_data.get('pk', 'unknown')}: {e}")

        return MediaPage(items, result.get("next_max_id", ""), len(raw_items))
