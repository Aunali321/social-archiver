import logging

from instagrapi.types import Media

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class SharedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client
        self._thread_id: int | None = None

    def fetch_shared_media(self, dm_username: str, amount: int = 0) -> list[SimpleMedia]:
        logger.info(f"Fetching shared media from DM with {dm_username} (amount={amount if amount > 0 else 'all'})")

        if self._thread_id is None:
            self._thread_id = self._get_thread_id(dm_username)

        if amount == 0:
            all_messages = []
            batch_size = 50
            while True:
                batch = self.ig_client.client.direct_messages(self._thread_id, amount=batch_size)
                if not batch:
                    break
                all_messages.extend(batch)
                if len(batch) < batch_size:
                    break
        else:
            all_messages = self.ig_client.client.direct_messages(self._thread_id, amount=amount)

        media_list = []
        seen_pks = set()

        for msg in all_messages:
            media = self._extract_media(msg)
            if media is None or str(media.pk) in seen_pks:
                continue
            seen_pks.add(str(media.pk))
            media_list.append(SimpleMedia.from_instagrapi(media, shared_by_username=self._sender_username(msg)))

        logger.info(f"Extracted {len(media_list)} shared media from {len(all_messages)} messages")
        return media_list

    def _extract_media(self, msg) -> Media | None:
        if msg.media_share:
            return msg.media_share
        if msg.clip:
            return msg.clip
        if getattr(msg, "reel_share", None) and getattr(msg.reel_share, "media", None):
            return msg.reel_share.media
        if getattr(msg, "story_share", None) and getattr(msg.story_share, "media", None):
            return msg.story_share.media
        return None

    def _sender_username(self, msg) -> str | None:
        if not msg.user_id:
            return None
        try:
            return self.ig_client.client.user_info(str(msg.user_id)).username
        except Exception as e:
            logger.warning(f"Failed to get sender username for user_id {msg.user_id}: {e}")
            return None

    def _get_thread_id(self, username: str) -> int:
        user_id = self.ig_client.get_user_id_by_username(username)
        result = self.ig_client.client.direct_thread_by_participants([int(user_id)])

        thread_data = result.get("thread", {})
        thread_id = thread_data.get("thread_id") or thread_data.get("thread_v2_id")
        if not thread_id:
            raise ValueError(f"Could not find thread_id in response for user {username}")

        logger.info(f"Found thread ID {thread_id} for user {username}")
        return int(thread_id)
