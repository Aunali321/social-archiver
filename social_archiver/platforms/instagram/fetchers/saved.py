import logging

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class SavedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    def fetch_saved_media(self, amount: int = 0) -> list[SimpleMedia]:
        logger.info(f"Fetching saved media from all collections (amount={amount if amount > 0 else 'all'})")

        collections = self.ig_client.client.collections()
        logger.info(f"Found {len(collections)} collections")

        all_posts_collection = next((c for c in collections if c.name == "All posts"), None)
        named_collections = [c for c in collections if c.name != "All posts"]

        all_media: list[SimpleMedia] = []
        seen_pks = set()

        for collection in named_collections:
            if amount > 0 and len(all_media) >= amount:
                break
            self._collect(collection.id, collection.name, amount, all_media, seen_pks)

        if all_posts_collection and (amount == 0 or len(all_media) < amount):
            self._collect(all_posts_collection.id, all_posts_collection.name, amount, all_media, seen_pks)

        logger.info(f"Fetched {len(all_media)} total saved media from {len(collections)} collections")
        return all_media[:amount] if amount > 0 else all_media

    def _collect(self, collection_pk: str, collection_name: str, amount: int, all_media: list, seen_pks: set) -> None:
        max_id = ""
        while amount == 0 or len(all_media) < amount:
            batch, next_max_id = self._fetch_collection_chunk(collection_pk, max_id, collection_name)
            if not batch:
                break
            for media in batch:
                if media.pk not in seen_pks:
                    all_media.append(media)
                    seen_pks.add(media.pk)
                    if amount > 0 and len(all_media) >= amount:
                        return
            if not next_max_id:
                break
            max_id = next_max_id

    def _fetch_collection_chunk(
        self, collection_pk: str, max_id: str, collection_name: str
    ) -> tuple[list[SimpleMedia], str]:
        if isinstance(collection_pk, int) or str(collection_pk).isdigit():
            endpoint = f"feed/collection/{collection_pk}/"
        elif str(collection_pk).lower() == "liked":
            endpoint = "feed/liked/"
        else:
            endpoint = "feed/saved/posts/"

        params = {"include_igtv_preview": "false"}
        if max_id:
            params["max_id"] = max_id

        result = self.ig_client.client.private_request(endpoint, params=params)

        items = []
        for item in result.get("items", []):
            media_data = item.get("media", item)
            try:
                media = SimpleMedia.from_dict(media_data)
                media.collection_name = collection_name
                items.append(media)
            except Exception as e:
                logger.warning(f"Failed to extract media {media_data.get('pk', 'unknown')}: {e}")

        return items, result.get("next_max_id", "")
