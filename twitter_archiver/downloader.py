import logging
import asyncio
import httpx
from pathlib import Path
from typing import List
from twitter_archiver.simple_tweet import SimpleTweet, TweetMedia
from twitter_archiver import config

logger = logging.getLogger(__name__)


class MediaDownloader:
    def __init__(self):
        pass

    async def download_tweet_media(
        self,
        tweet: SimpleTweet,
        category: str,
        max_retries: int = config.MAX_DOWNLOAD_RETRIES,
    ) -> List[Path]:
        """Download all media from a tweet. Returns list of local paths."""
        if not tweet.has_media:
            return []

        folder = self._get_folder(category)
        folder.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, max_retries + 1):
            try:
                paths = await self._download_all_media(tweet, folder)
                logger.info(f"Downloaded {tweet.id}: {len(paths)} file(s)")
                return paths
            except Exception as e:
                if attempt == max_retries:
                    logger.error(
                        f"Failed to download {tweet.id} after {max_retries} attempts: {e}"
                    )
                    raise

                backoff = config.RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    f"Download attempt {attempt} failed for {tweet.id}, retrying in {backoff}s: {e}"
                )
                await asyncio.sleep(backoff)

        raise RuntimeError(f"Failed to download {tweet.id}")

    async def _download_all_media(
        self, tweet: SimpleTweet, folder: Path
    ) -> List[Path]:
        paths = []
        for idx, media in enumerate(tweet.media):
            path = await self._download_single_media(tweet.id, media, folder, idx)
            if path:
                paths.append(path)
        return paths

    async def _download_single_media(
        self, tweet_id: str, media: TweetMedia, folder: Path, idx: int
    ) -> Path:
        if media.type == "photo":
            # Download highest quality photo
            # Twitter photo URLs support :orig suffix for original quality
            url = f"{media.url}:orig"
            ext = self._get_photo_extension(media.url)
            filename = f"{tweet_id}_{idx}.{ext}" if idx > 0 else f"{tweet_id}.{ext}"
            path = folder / filename
            await self._download_url(url, path)
            return path

        elif media.type in ("video", "animated_gif"):
            if not media.video_url:
                raise ValueError(f"No video URL for {media.type} in tweet {tweet_id}")
            filename = f"{tweet_id}_{idx}.mp4" if idx > 0 else f"{tweet_id}.mp4"
            path = folder / filename
            await self._download_url(media.video_url, path)
            return path

        else:
            logger.warning(f"Unknown media type: {media.type}")
            return None  # type: ignore

    async def _download_url(self, url: str, path: Path) -> None:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            path.write_bytes(response.content)
            logger.debug(f"Downloaded {url} -> {path}")

    def _get_photo_extension(self, url: str) -> str:
        """Infer photo extension from URL."""
        if ".png" in url:
            return "png"
        if ".gif" in url:
            return "gif"
        if ".webp" in url:
            return "webp"
        return "jpg"

    def _get_folder(self, category: str) -> Path:
        folder_map = {
            "bookmarks": config.DOWNLOADS_BOOKMARKS,
            "likes": config.DOWNLOADS_LIKES,
        }
        return folder_map.get(category, config.DOWNLOADS_DIR / category)
