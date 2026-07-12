import logging

from instagrapi.types import Media

from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)


class SharedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client
        self._thread_id: int | None = None
        self._sender_map: dict[str, str] = {}

    def fetch_shared_media(self, dm_username: str, amount: int = 0) -> list[SimpleMedia | Media]:
        logger.info(f"Fetching shared media from DM with {dm_username} (amount={amount if amount > 0 else 'all'})")

        if self._thread_id is None:
            self._thread_id = self._get_thread_id(dm_username)

        self._sender_map.clear()

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
            if media and media.pk not in seen_pks:
                if msg.user_id:
                    try:
                        sender = self.ig_client.client.user_info(str(msg.user_id)).username
                        self._sender_map[str(media.pk)] = sender
                    except Exception as e:
                        logger.warning(f"Failed to get sender username for user_id {msg.user_id}: {e}")
                media_list.append(media)
                seen_pks.add(media.pk)

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

    def get_sender_username(self, media_pk: str) -> str | None:
        return self._sender_map.get(str(media_pk))

    def _get_thread_id(self, username: str) -> int:
        user_id = self.ig_client.get_user_id_by_username(username)
        result = self.ig_client.client.direct_thread_by_participants([int(user_id)])

        thread_data = result.get("thread", {})
        thread_id = thread_data.get("thread_id") or thread_data.get("thread_v2_id")
        if not thread_id:
            raise ValueError(f"Could not find thread_id in response for user {username}")

        logger.info(f"Found thread ID {thread_id} for user {username}")
        return int(thread_id)
