import logging
import asyncio
import httpx
from pathlib import Path
from typing import List, Union
from instagrapi.types import Media
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.simple_media import SimpleMedia, SimpleResource
from insta_archiver import config

logger = logging.getLogger(__name__)

class MediaDownloader:
    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client
    
    async def download_media(
        self,
        media: Union[Media, SimpleMedia],
        category: str,
        max_retries: int = config.MAX_DOWNLOAD_RETRIES
    ) -> List[Path]:
        folder = self._get_folder(category, media)
        
        for attempt in range(1, max_retries + 1):
            try:
                paths = await self._download_with_type(media, folder)
                logger.info(f"Downloaded {media.pk} to {len(paths)} file(s)")
                return paths
            except Exception as e:
                if attempt == max_retries:
                    logger.error(f"Failed to download {media.pk} after {max_retries} attempts: {e}")
                    raise
                
                backoff = config.RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(f"Download attempt {attempt} failed for {media.pk}, retrying in {backoff}s: {e}")
                await asyncio.sleep(backoff)
        
        raise RuntimeError(f"Failed to download {media.pk}")
    
    async def _download_with_type(self, media: Union[Media, SimpleMedia], folder: Path) -> List[Path]:
        # For SimpleMedia, use direct URL download (bypasses validation errors)
        if isinstance(media, SimpleMedia):
            return await self._download_simple_media(media, folder)
        
        # For Media (from shared/DM), use instagrapi methods (should work for those)
        return await self._download_instagrapi_media(media, folder)
    
    async def _download_simple_media(self, media: SimpleMedia, folder: Path) -> List[Path]:
        """Download SimpleMedia using direct URLs (no instagrapi validation)"""
        folder.mkdir(parents=True, exist_ok=True)
        
        if media.media_type == 1:
            # Photo
            if not media.thumbnail_url:
                raise ValueError(f"No thumbnail URL for photo {media.pk}")
            filename = f"{media.pk}.jpg"
            path = folder / filename
            await self._download_url(media.thumbnail_url, path)
            return [path]
        
        elif media.media_type == 2:
            # Video
            if media.product_type == "igtv":
                logger.info(f"Skipping IGTV post {media.pk}")
                return []
            
            if not media.video_url:
                raise ValueError(f"No video URL for video {media.pk}")
            
            filename = f"{media.pk}.mp4"
            path = folder / filename
            await self._download_url(media.video_url, path)
            return [path]
        
        elif media.media_type == 8:
            # Album/carousel
            paths = []
            for idx, resource in enumerate(media.resources):
                if resource.media_type == 1:
                    # Photo in album
                    if not resource.thumbnail_url:
                        continue
                    filename = f"{media.pk}_{idx + 1}.jpg"
                    path = folder / filename
                    await self._download_url(resource.thumbnail_url, path)
                    paths.append(path)
                elif resource.media_type == 2:
                    # Video in album
                    if not resource.video_url:
                        continue
                    filename = f"{media.pk}_{idx + 1}.mp4"
                    path = folder / filename
                    await self._download_url(resource.video_url, path)
                    paths.append(path)
            
            return paths
        
        else:
            raise ValueError(f"Unsupported media type: {media.media_type}")
    
    async def _download_url(self, url, path: Path) -> None:
        """Download a file from URL using httpx"""
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(str(url))
            response.raise_for_status()
            path.write_bytes(response.content)
            logger.debug(f"Downloaded {url} to {path}")
    
    async def _download_instagrapi_media(self, media: Media, folder: Path) -> List[Path]:
        """
        Download Media using direct URLs (avoiding instagrapi's media_info validation).
        Media from DMs already has URLs populated, so we can download directly.
        """
        folder.mkdir(parents=True, exist_ok=True)
        
        if media.media_type == 1:
            # Photo
            if not media.thumbnail_url:
                raise ValueError(f"No thumbnail URL for photo {media.pk}")
            filename = f"{media.pk}.jpg"
            path = folder / filename
            await self._download_url(media.thumbnail_url, path)
            return [path]
        
        elif media.media_type == 2:
            # Video/Clip
            if media.product_type == "igtv":
                logger.info(f"Skipping IGTV post {media.pk}")
                return []
            
            if not media.video_url:
                raise ValueError(f"No video URL for video {media.pk}")
            
            filename = f"{media.pk}.mp4"
            path = folder / filename
            await self._download_url(media.video_url, path)
            return [path]
        
        elif media.media_type == 8:
            # Album
            paths = []
            for idx, resource in enumerate(media.resources):
                if resource.media_type == 1:
                    # Photo in album
                    if not resource.thumbnail_url:
                        continue
                    filename = f"{media.pk}_{idx + 1}.jpg"
                    path = folder / filename
                    await self._download_url(resource.thumbnail_url, path)
                    paths.append(path)
                elif resource.media_type == 2:
                    # Video in album
                    if not resource.video_url:
                        continue
                    filename = f"{media.pk}_{idx + 1}.mp4"
                    path = folder / filename
                    await self._download_url(resource.video_url, path)
                    paths.append(path)
            
            return paths
        
        else:
            raise ValueError(f"Unsupported media type: {media.media_type}")
    
    def _get_folder(self, category: str, media: Union[Media, SimpleMedia] = None) -> Path:  # type: ignore
        folder_map = {
            "likes": config.DOWNLOADS_LIKES,
            "saved": config.DOWNLOADS_SAVED,
            "shared": config.DOWNLOADS_SHARED,
        }
        base_folder = folder_map[category]
        
        # For saved posts, create subfolder by collection name
        if category == "saved" and media and isinstance(media, SimpleMedia) and media.collection_name:
            # Sanitize collection name for filesystem
            safe_collection_name = "".join(c for c in media.collection_name if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_collection_name:
                safe_collection_name = "uncategorized"
            return base_folder / safe_collection_name
        
        return base_folder
