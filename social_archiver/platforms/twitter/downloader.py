import logging
from pathlib import Path

from social_archiver.core.downloader import download_with_retry, fetch_url
from social_archiver.platforms.twitter import config
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet, TweetMedia

logger = logging.getLogger(__name__)


class MediaDownloader:
    async def download_tweet_media(
        self, tweet: SimpleTweet, folder: Path = config.DOWNLOADS_LIKES, max_retries: int = config.MAX_DOWNLOAD_RETRIES
    ) -> list[Path]:
        if not tweet.has_media:
            return []

        folder.mkdir(parents=True, exist_ok=True)

        paths = await download_with_retry(
            lambda: self._download_all_media(tweet, folder), tweet.id, max_retries, config.RETRY_BACKOFF_BASE
        )
        logger.info(f"Downloaded {tweet.id}: {len(paths)} file(s)")
        return paths

    async def _download_all_media(self, tweet: SimpleTweet, folder: Path) -> list[Path]:
        paths = []
        for idx, media in enumerate(tweet.media):
            path = await self._download_single_media(tweet.id, media, folder, idx)
            if path:
                paths.append(path)
        return paths

    async def _download_single_media(self, tweet_id: str, media: TweetMedia, folder: Path, idx: int) -> Path | None:
        suffix = f"_{idx}" if idx > 0 else ""

        if media.type == "photo":
            path = folder / f"{tweet_id}{suffix}.{self._get_photo_extension(media.url)}"
            await fetch_url(f"{media.url}:orig", path, timeout=120.0)
            return path

        if media.type in ("video", "animated_gif"):
            if not media.video_url:
                raise ValueError(f"No video URL for {media.type} in tweet {tweet_id}")
            path = folder / f"{tweet_id}{suffix}.mp4"
            await fetch_url(media.video_url, path, timeout=120.0)
            return path

        logger.warning(f"Unknown media type: {media.type}")
        return None

    def _get_photo_extension(self, url: str) -> str:
        for ext in ("png", "gif", "webp"):
            if f".{ext}" in url:
                return ext
        return "jpg"
