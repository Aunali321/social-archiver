import logging
from typing import List
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

class LikesFetcher:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client
    
    def fetch_liked_media(self, amount: int = 0) -> List[SimpleMedia]:
        logger.info(f"Fetching liked media (amount={amount if amount > 0 else 'all'})")
        
        if amount == 0:
            all_media = []
            batch_size = 50
            max_id = ""
            
            while True:
                batch, next_max_id = self._fetch_liked_chunk(max_id)
                if not batch:
                    break
                
                all_media.extend(batch)
                logger.debug(f"Fetched {len(batch)} items, total: {len(all_media)}")
                
                if not next_max_id:
                    break
                max_id = next_max_id
            
            logger.info(f"Fetched {len(all_media)} total liked media")
            return all_media
        else:
            # For limited amount, fetch in chunks until we have enough
            all_media = []
            max_id = ""
            
            while len(all_media) < amount:
                batch, next_max_id = self._fetch_liked_chunk(max_id)
                if not batch:
                    break
                
                all_media.extend(batch)
                
                if not next_max_id:
                    break
                max_id = next_max_id
            
            logger.info(f"Fetched {len(all_media[:amount])} liked media")
            return all_media[:amount]
    
    def _fetch_liked_chunk(self, max_id: str = "") -> tuple[List[SimpleMedia], str]:
        """Fetch a chunk of liked media using raw API and normalize problematic fields"""
        params = {"include_igtv_preview": "false"}
        if max_id:
            params["max_id"] = max_id
        
        result = self.ig_client.client.private_request("feed/liked/", params=params)
        
        # Use our forgiving SimpleMedia extractor instead of instagrapi's strict one
        items = []
        for item in result.get("items", []):
            media_data = item.get("media", item)
            try:
                items.append(SimpleMedia.from_dict(media_data))
            except Exception as e:
                logger.warning(f"Failed to extract media {media_data.get('pk', 'unknown')}: {e}")
                continue
        
        next_max_id = result.get("next_max_id", "")
        return items, next_max_id
    
    def _normalize_clips_metadata(self, media: dict) -> None:
        """Normalize clips_metadata to handle None values that cause Pydantic validation errors"""
        if not media.get("clips_metadata") or not isinstance(media["clips_metadata"], dict):
            return
        
        clips = media["clips_metadata"]
        
        # Handle original_sound_info which can be None or have None sub-fields
        osi = clips.get("original_sound_info")
        if osi is None:
            # If original_sound_info is None, remove it so Pydantic uses default
            clips.pop("original_sound_info", None)
        elif isinstance(osi, dict):
            # Normalize list fields that can be None
            osi["audio_filter_infos"] = osi.get("audio_filter_infos") or []
            osi["audio_parts"] = osi.get("audio_parts") or []
            osi["audio_parts_by_filter"] = osi.get("audio_parts_by_filter") or []
