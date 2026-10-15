"""Fetching the avatars and banners sightings named.

Kept in the archive rather than linked to, because the platforms expire their links: an
Instagram avatar URL stops working within days, and an account that changes its picture takes
the old one down. Each picture is a file under the platform's `profiles/` folder, which no
item owns, so the media cleanup never reaches it.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

from social_archiver.core import config
from social_archiver.core.database import Database
from social_archiver.core.downloader import download_with_retry, fetch_url, link_file, url_extension
from social_archiver.core.profile_store import PendingImage

logger = logging.getLogger(__name__)


def _target(platform: str, image: PendingImage) -> Path:
    """Named by the picture's key, so the same picture seen again lands on the same file."""
    digest = hashlib.sha256(image.key.encode()).hexdigest()[:16]
    match image.source:
        case Path() as file:
            suffix = file.suffix or ".jpg"
        case url:
            suffix = url_extension(url)
    folder = config.DOWNLOADS_DIR / platform / "profiles" / image.profile_id.replace("/", "_")
    return folder / f"{image.kind}-{digest}{suffix}"


async def _fetch(platform: str, image: PendingImage) -> Path:
    target = _target(platform, image)
    match image.source:
        case Path() as file:
            link_file(file, target)
        case url:
            target.parent.mkdir(parents=True, exist_ok=True)
            await download_with_retry(
                lambda: fetch_url(url, target),
                f"{image.kind} of {image.profile_id}",
                config.MAX_DOWNLOAD_RETRIES,
                config.RETRY_BACKOFF_BASE,
            )
    return target


async def download_profile_images(db: Database, platform: str, retry_failed: bool = False):
    pending = await db.profiles.pending_images(retry_failed)
    if not pending:
        return
    logger.info(f"Fetching {len(pending)} profile picture(s)")
    semaphore = asyncio.Semaphore(config.DOWNLOAD_CONCURRENCY)
    fetched = failed = 0

    async def fetch(image: PendingImage):
        nonlocal fetched, failed
        async with semaphore:
            try:
                path = await _fetch(platform, image)
            except Exception as e:
                await db.profiles.mark_image_failed(image, str(e))
                failed += 1
                return
            await db.profiles.mark_image_fetched(image, path)
            fetched += 1

    await asyncio.gather(*(fetch(image) for image in pending))
    logger.info(f"Profile pictures: {fetched} fetched, {failed} failed")
