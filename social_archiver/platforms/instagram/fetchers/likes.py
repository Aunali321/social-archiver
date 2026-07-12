import logging

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class LikesFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    def fetch_liked_media(self, amount: int = 0) -> list[SimpleMedia]:
        logger.info(f"Fetching liked media (amount={amount if amount > 0 else 'all'})")

        all_media: list[SimpleMedia] = []
        max_id = ""

        while amount == 0 or len(all_media) < amount:
            batch, next_max_id = self._fetch_liked_chunk(max_id)
            if not batch:
                break
            all_media.extend(batch)
            if not next_max_id:
                break
            max_id = next_max_id

        result = all_media[:amount] if amount > 0 else all_media
        logger.info(f"Fetched {len(result)} liked media")
        return result

    def _fetch_liked_chunk(self, max_id: str = "") -> tuple[list[SimpleMedia], str]:
        """Fetch a chunk of liked media, tolerating fields that break instagrapi's strict extractor."""
        params = {"include_igtv_preview": "false"}
        if max_id:
            params["max_id"] = max_id

        result = self.ig_client.client.private_request("feed/liked/", params=params)

        items = []
        for item in result.get("items", []):
            media_data = item.get("media", item)
            try:
                items.append(SimpleMedia.from_dict(media_data))
            except Exception as e:
                logger.warning(f"Failed to extract media {media_data.get('pk', 'unknown')}: {e}")

        return items, result.get("next_max_id", "")
