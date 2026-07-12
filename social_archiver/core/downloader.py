import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
