import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.page import FEED_PAGE_SIZE, MediaPage
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

ALL_POSTS = "All posts"


@dataclass(frozen=True, slots=True)
class SavedCursor:
    """Saved is one feed per collection, so a position is a collection and an offset in
    it. Collections are walked in id order rather than the order the API returns them,
    so that everything sorted before `collection_id` is known to be finished."""

    collection_id: str
    max_id: str

    def __str__(self) -> str:
        return f"{self.collection_id}:{self.max_id}"

    @classmethod
    def parse(cls, raw: str) -> "SavedCursor | None":
        collection_id, separator, max_id = raw.partition(":")
        return cls(collection_id, max_id) if separator and collection_id else None


class SavedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    def fetch_saved_media(self, amount: int = 0, start_cursor: str = "") -> Iterator[MediaPage]:
        """Yield one API page at a time, named collections before the "All posts"
        catch-all so a post in both is recorded under its real collection name.
        Cross-collection duplicates are dropped by the caller."""
        logger.info(f"Fetching saved media from all collections (amount={amount if amount > 0 else 'all'})")

        collections = sorted(self.ig_client.client.collections(), key=lambda c: (c.name == ALL_POSTS, str(c.id)))
        logger.info(f"Found {len(collections)} collections")

        pending, max_id = self._resume(collections, start_cursor)

        fetched = 0
        for index, collection in enumerate(pending):
            if amount > 0 and fetched >= amount:
                break
            for page in self._collect(collection.id, collection.name, amount - fetched if amount else 0, max_id):
                fetched += len(page.media)
                yield replace(page, resume_from=str(self._next_position(pending, index, page.next_max_id)))
            max_id = ""

        logger.info(f"Fetched {fetched} saved media from {len(collections)} collections")

    def _resume(self, collections: Sequence, start_cursor: str) -> tuple[Sequence, str]:
        cursor = SavedCursor.parse(start_cursor)
        if not cursor:
            return collections, ""

        ids = [str(c.id) for c in collections]
        if cursor.collection_id not in ids:
            logger.warning(f"Cursor names collection {cursor.collection_id}, which is gone; starting over")
            return collections, ""

        done = ids.index(cursor.collection_id)
        logger.info(f"Resuming in {collections[done].name}, {done} collection(s) already walked")
        return collections[done:], cursor.max_id

    @staticmethod
    def _next_position(pending: Sequence, index: int, next_max_id: str) -> SavedCursor:
        """An exhausted collection advances to the next one, so a resumed walk does not
        pay for a feed it has already finished."""
        if next_max_id:
            return SavedCursor(str(pending[index].id), next_max_id)
        following = min(index + 1, len(pending) - 1)
        return SavedCursor(str(pending[following].id), "")

    def _collect(self, collection_pk: str, collection_name: str, amount: int, max_id: str = "") -> Iterator[MediaPage]:
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
