import logging
from collections.abc import Iterator

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.page import FEED_PAGE_SIZE, MediaPage
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

ALL_POSTS = "All posts"


class SavedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    def fetch_saved_media(self, amount: int = 0) -> Iterator[MediaPage]:
        """Yield one API page at a time, named collections before the "All posts"
        catch-all so a post in both is recorded under its real collection name.
        Cross-collection duplicates are dropped by the caller."""
        logger.info(f"Fetching saved media from all collections (amount={amount if amount > 0 else 'all'})")

        collections = self.ig_client.client.collections()
        logger.info(f"Found {len(collections)} collections")

        fetched = 0
        for collection in sorted(collections, key=lambda c: c.name == ALL_POSTS):
            if amount > 0 and fetched >= amount:
                break
            for page in self._collect(collection.id, collection.name, amount - fetched if amount else 0):
                fetched += len(page.media)
                yield page

        logger.info(f"Fetched {fetched} saved media from {len(collections)} collections")

    def _collect(self, collection_pk: str, collection_name: str, amount: int) -> Iterator[MediaPage]:
        max_id = ""
        fetched = 0
        while amount == 0 or fetched < amount:
            page = self._fetch_collection_chunk(collection_pk, max_id, collection_name)
            media = page.media[: amount - fetched] if amount > 0 else page.media
            fetched += len(media)
            if media:
                yield MediaPage(media, page.next_max_id, page.raw_count)
            if not page.has_more:
                break
            max_id = page.next_max_id

    def _fetch_collection_chunk(self, collection_pk: str, max_id: str, collection_name: str) -> MediaPage:
        if isinstance(collection_pk, int) or str(collection_pk).isdigit():
            endpoint = f"feed/collection/{collection_pk}/"
        elif str(collection_pk).lower() == "liked":
            endpoint = "feed/liked/"
        else:
            endpoint = "feed/saved/posts/"

        params = {"include_igtv_preview": "false", "count": str(FEED_PAGE_SIZE)}
        if max_id:
            params["max_id"] = max_id

        result = self.ig_client.private_request(endpoint, params)
        raw_items = result.get("items", [])

        items = []
        for item in raw_items:
            media_data = item.get("media", item)
            try:
                media = SimpleMedia.from_dict(media_data)
                media.collection_name = collection_name
                items.append(media)
            except Exception as e:
                logger.warning(f"Failed to extract media {media_data.get('pk', 'unknown')}: {e}")

        return MediaPage(items, result.get("next_max_id", ""), len(raw_items))
