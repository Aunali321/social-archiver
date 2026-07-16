import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_KNOWN_EXTENSIONS = frozenset({".mp4", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"})


def url_extension(url: str) -> str:
    parts = urlsplit(url.removesuffix(":orig"))
    ext = Path(parts.path).suffix.lower()
    if ext in _KNOWN_EXTENSIONS:
        return ext
    if fmt := parse_qs(parts.query).get("format"):
        return f".{fmt[0]}"
    return ".jpg"


async def download_urls(urls: list[str], folder: Path, stem: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, url in enumerate(urls):
        suffix = f"_{idx}" if len(urls) > 1 else ""
        path = folder / f"{stem}{suffix}{url_extension(url)}"
        await fetch_url(url, path, timeout=120.0)
        paths.append(path)
    return paths


async def fetch_url(url: str, path: Path, timeout: float = 60.0) -> None:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(str(url))
        response.raise_for_status()
        path.write_bytes(response.content)
        logger.debug(f"Downloaded {url} -> {path}")


async def download_with_retry(
    download_fn: Callable[[], Awaitable[T]], item_label: str, max_retries: int, backoff_base: int
) -> T:
    for attempt in range(1, max_retries + 1):
        try:
            return await download_fn()
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Failed to download {item_label} after {max_retries} attempts: {e}")
                raise
            backoff = backoff_base * (2 ** (attempt - 1))
            logger.warning(f"Download attempt {attempt} failed for {item_label}, retrying in {backoff}s: {e}")
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Failed to download {item_label}")
