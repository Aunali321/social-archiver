import asyncio
import logging
import sys
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_KNOWN_EXTENSIONS = frozenset({".mp4", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"})

# HTTP statuses that won't recover on retry (dead/removed/forbidden media).
_PERMANENT_STATUS = frozenset({400, 401, 403, 404, 410})


def url_extension(url: str) -> str:
    """A `format` parameter is the server stating what it actually serves, so it outranks
    the path suffix: Reddit hands out `<id>.gif?format=mp4` for animated media, where
    trusting the suffix would write mp4 bytes into a .gif."""
    parts = urlsplit(url.removesuffix(":orig"))

    if fmt := parse_qs(parts.query).get("format"):
        if (ext := f".{fmt[0].lower()}") in _KNOWN_EXTENSIONS:
            return ext

    ext = Path(parts.path).suffix.lower()
    return ext if ext in _KNOWN_EXTENSIONS else ".jpg"


def is_direct_media(url: str) -> bool:
    """Whether a URL points straight at a downloadable media file, versus a page or
    adaptive stream (v.redd.it DASH, YouTube, ...) that an extractor must resolve
    and mux before it can be saved."""
    return Path(urlsplit(url.removesuffix(":orig")).path).suffix.lower() in _KNOWN_EXTENSIONS


async def download_urls(urls: list[str], folder: Path, stem: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, url in enumerate(urls):
        suffix = f"_{idx}" if len(urls) > 1 else ""
        if is_direct_media(url):
            path = folder / f"{stem}{suffix}{url_extension(url)}"
            await fetch_url(url, path, timeout=120.0)
        else:
            path = await fetch_stream(url, folder, f"{stem}{suffix}")
        paths.append(path)
    return paths


async def fetch_stream(url: str, folder: Path, stem: str) -> Path:
    """Download a video through yt-dlp, muxing separate audio and video tracks
    (e.g. v.redd.it DASH) into one mp4 with ffmpeg. Returns the produced file."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-progress",
        "--quiet",
        "--no-warnings",
        "--merge-output-format",
        "mp4",
        "--no-simulate",
        "--print",
        "after_move:filepath",
        "-o",
        str(folder / f"{stem}.%(ext)s"),
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {url}: {stderr.decode().strip()[-500:]}")
    produced = [line.strip() for line in stdout.decode().splitlines() if line.strip()]
    if not produced:
        raise RuntimeError(f"yt-dlp produced no output file for {url}")
    return Path(produced[-1])


async def fetch_url(url: str, path: Path, timeout: float = 60.0) -> None:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(str(url))
        response.raise_for_status()
        path.write_bytes(response.content)
        logger.debug(f"Downloaded {url} -> {path}")


async def download_with_retry(
    download_fn: Callable[[], Awaitable[T]],
    item_label: str,
    max_retries: int,
    backoff_base: int,
) -> T:
    for attempt in range(1, max_retries + 1):
        try:
            return await download_fn()
        except Exception as e:
            # Permanent failures (dead links) won't recover; don't waste the backoff on them
            permanent = isinstance(e, httpx.HTTPStatusError) and e.response.status_code in _PERMANENT_STATUS
            if permanent or attempt == max_retries:
                logger.error(f"Failed to download {item_label} after {attempt} attempt(s): {e}")
                raise
            backoff = backoff_base * (2 ** (attempt - 1))
            logger.warning(f"Download attempt {attempt} failed for {item_label}, retrying in {backoff}s: {e}")
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Failed to download {item_label}")
