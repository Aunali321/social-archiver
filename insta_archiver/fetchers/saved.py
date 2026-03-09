import logging
from typing import List
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class SavedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    def fetch_saved_media(self, amount: int = 0) -> List[SimpleMedia]:
        logger.info(
            f"Fetching saved media from all collections (amount={amount if amount > 0 else 'all'})"
        )

        collections = self.ig_client.client.collections()
        logger.info(f"Found {len(collections)} collections")

        # Separate 'All posts' from named collections
        all_posts_collection = None
        named_collections = []

        for collection in collections:
            if collection.name == "All posts":
                all_posts_collection = collection
            else:
                named_collections.append(collection)

        all_media = []
        seen_pks = set()

        # Process named collections first (for granularity)
        for collection in named_collections:
            logger.debug(f"Fetching from collection: {collection.name}")

            if amount == 0:
                collection_media = self._fetch_all_from_collection(
                    collection.id, collection.name
                )
            else:
                remaining = amount - len(all_media)
                if remaining <= 0:
                    break
                collection_media = self._fetch_from_collection(
                    collection.id, remaining, collection.name
                )

            for media in collection_media:
                if media.pk not in seen_pks:
                    all_media.append(media)
                    seen_pks.add(media.pk)
                    if amount > 0 and len(all_media) >= amount:
                        break

            if amount > 0 and len(all_media) >= amount:
                break

        # Then process 'All posts' for items not in any named collection
        if all_posts_collection and (amount == 0 or len(all_media) < amount):
            logger.debug(f"Fetching from collection: {all_posts_collection.name}")

            if amount == 0:
                collection_media = self._fetch_all_from_collection(
                    all_posts_collection.id, all_posts_collection.name
                )
            else:
                remaining = amount - len(all_media)
                collection_media = self._fetch_from_collection(
                    all_posts_collection.id, remaining, all_posts_collection.name
                )

            for media in collection_media:
                if media.pk not in seen_pks:
                    all_media.append(media)
                    seen_pks.add(media.pk)
                    if amount > 0 and len(all_media) >= amount:
                        break

        logger.info(
            f"Fetched {len(all_media)} total saved media from {len(collections)} collections"
        )
        return all_media[:amount] if amount > 0 else all_media

    def _fetch_all_from_collection(
        self, collection_pk: str, collection_name: str
    ) -> List[SimpleMedia]:
        """Fetch all media from a collection"""
        all_media = []
        max_id = ""

        while True:
            batch, next_max_id = self._fetch_collection_chunk(
                collection_pk, max_id, collection_name
            )
            if not batch:
                break

            all_media.extend(batch)

            if not next_max_id:
                break
            max_id = next_max_id

        return all_media

    def _fetch_from_collection(
        self, collection_pk: str, amount: int, collection_name: str
    ) -> List[SimpleMedia]:
        """Fetch limited amount of media from a collection"""
        all_media = []
        max_id = ""

        while len(all_media) < amount:
            batch, next_max_id = self._fetch_collection_chunk(
                collection_pk, max_id, collection_name
            )
            if not batch:
                break

            all_media.extend(batch)

            if not next_max_id:
                break
            max_id = next_max_id

        return all_media[:amount]

    def _fetch_collection_chunk(
        self, collection_pk: str, max_id: str = "", collection_name: str = ""
    ) -> tuple[List[SimpleMedia], str]:
        """Fetch a chunk of media from a collection using raw API and normalize problematic fields"""
        if isinstance(collection_pk, int) or collection_pk.isdigit():
            endpoint = f"feed/collection/{collection_pk}/"
        elif collection_pk.lower() == "liked":
            endpoint = "feed/liked/"
        else:
            endpoint = "feed/saved/posts/"

        params = {"include_igtv_preview": "false"}
        if max_id:
            params["max_id"] = max_id

        result = self.ig_client.client.private_request(endpoint, params=params)

        # Use our forgiving SimpleMedia extractor instead of instagrapi's strict one
        items = []
        for item in result.get("items", []):
            media_data = item.get("media", item)
            try:
                media = SimpleMedia.from_dict(media_data)
                # Set collection name on the media object
                media.collection_name = collection_name
                items.append(media)
            except Exception as e:
                logger.warning(
                    f"Failed to extract media {media_data.get('pk', 'unknown')}: {e}"
                )
                continue

        next_max_id = result.get("next_max_id", "")
        return items, next_max_id
