import logging
from pathlib import Path

from instagrapi.types import Media

from social_archiver.core.downloader import download_with_retry, fetch_url
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

AnyMedia = SimpleMedia | Media


class MediaDownloader:
    async def download_media(
        self, media: AnyMedia, category: str, max_retries: int = config.MAX_DOWNLOAD_RETRIES
    ) -> list[Path]:
        folder = self._get_folder(category, media)
        paths = await download_with_retry(
            lambda: self._download(media, folder), str(media.pk), max_retries, config.RETRY_BACKOFF_BASE
        )
        logger.info(f"Downloaded {media.pk} to {len(paths)} file(s)")
        return paths

    async def _download(self, media: AnyMedia, folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)

        if media.media_type == 1:
            if not media.thumbnail_url:
                raise ValueError(f"No thumbnail URL for photo {media.pk}")
            path = folder / f"{media.pk}.jpg"
            await fetch_url(media.thumbnail_url, path)
            return [path]

        if media.media_type == 2:
            if media.product_type == "igtv":
                logger.info(f"Skipping IGTV post {media.pk}")
                return []
            if not media.video_url:
                raise ValueError(f"No video URL for video {media.pk}")
            path = folder / f"{media.pk}.mp4"
            await fetch_url(media.video_url, path)
            return [path]

        if media.media_type == 8:
            paths = []
            for idx, resource in enumerate(media.resources):
                if resource.media_type == 1 and resource.thumbnail_url:
                    path = folder / f"{media.pk}_{idx + 1}.jpg"
                    await fetch_url(resource.thumbnail_url, path)
                    paths.append(path)
                elif resource.media_type == 2 and resource.video_url:
                    path = folder / f"{media.pk}_{idx + 1}.mp4"
                    await fetch_url(resource.video_url, path)
                    paths.append(path)
            return paths

        raise ValueError(f"Unsupported media type: {media.media_type}")

    def _get_folder(self, category: str, media: AnyMedia) -> Path:
        base_folder = {
            "likes": config.DOWNLOADS_LIKES,
            "saved": config.DOWNLOADS_SAVED,
            "shared": config.DOWNLOADS_SHARED,
        }[category]

        if category == "saved" and isinstance(media, SimpleMedia) and media.collection_name:
            safe_name = "".join(c for c in media.collection_name if c.isalnum() or c in (" ", "-", "_")).strip()
            return base_folder / (safe_name or "uncategorized")

        return base_folder
