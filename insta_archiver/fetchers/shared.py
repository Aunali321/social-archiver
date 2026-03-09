import logging
from typing import List, Union, Dict
from instagrapi.types import Media
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

class SharedFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client
        self._thread_id: int = None  # type: ignore
        # Map media_pk to sender username
        self._sender_map: Dict[str, str] = {}
    
    def fetch_shared_media(self, dm_username: str, amount: int = 0) -> List[Union[Media, SimpleMedia]]:
        logger.info(f"Fetching shared media from DM with {dm_username} (amount={amount if amount > 0 else 'all'})")
        
        if self._thread_id is None:
            self._thread_id = self._get_thread_id(dm_username)
        
        # Clear the sender map for fresh fetch
        self._sender_map.clear()
        
        if amount == 0:
            all_messages = []
            batch_size = 50
            
            while True:
                batch = self.ig_client.client.direct_messages(self._thread_id, amount=batch_size)
                if not batch:
                    break
                
                all_messages.extend(batch)
                logger.debug(f"Fetched {len(batch)} messages, total: {len(all_messages)}")
                
                if len(batch) < batch_size:
                    break
        else:
            all_messages = self.ig_client.client.direct_messages(self._thread_id, amount=amount)
        
        media_list = []
        seen_pks = set()
        
        for msg in all_messages:
            media = None
            
            if msg.media_share:
                media = msg.media_share
            elif msg.clip:
                # Clips (reels) shared in DM
                media = msg.clip
            elif hasattr(msg, 'reel_share') and msg.reel_share and hasattr(msg.reel_share, 'media'):
                media = msg.reel_share.media  # type: ignore
            elif hasattr(msg, 'story_share') and msg.story_share and hasattr(msg.story_share, 'media'):
                media = msg.story_share.media  # type: ignore
            
            if media and media.pk not in seen_pks:
                # Store sender info in the map
                if msg.user_id:
                    try:
                        sender_username = self.ig_client.client.user_info(str(msg.user_id)).username
                        self._sender_map[str(media.pk)] = sender_username
                        logger.debug(f"Media {media.pk} shared by @{sender_username}")
                    except Exception as e:
                        logger.warning(f"Failed to get sender username for user_id {msg.user_id}: {e}")
                
                media_list.append(media)
                seen_pks.add(media.pk)
        
        logger.info(f"Extracted {len(media_list)} shared media from {len(all_messages)} messages")
        return media_list
    
    def get_sender_username(self, media_pk: str) -> str:
        """Get the username of who shared a specific media"""
        return self._sender_map.get(str(media_pk), None)  # type: ignore
    
    def _get_thread_id(self, username: str) -> int:
        user_id = self.ig_client.get_user_id_by_username(username)
        result = self.ig_client.client.direct_thread_by_participants([int(user_id)])
        
        # direct_thread_by_participants returns a dict, not a DirectThread object
        # Extract thread_id from the nested structure
        thread_data = result.get("thread", {})
        thread_id = thread_data.get("thread_id") or thread_data.get("thread_v2_id")
        
        if not thread_id:
            raise ValueError(f"Could not find thread_id in response for user {username}")
        
        logger.info(f"Found thread ID {thread_id} for user {username}")
        return int(thread_id)
