import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_KNOWN_EXTENSIONS = frozenset({".mp4", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"})

_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

_YT_DLP_STATUS = re.compile(r"HTTP Error (\d{3})")


class ExtractorError(RuntimeError):
    """yt-dlp exits 1 for every failure, so the status it hit exists only in its stderr."""

    def __init__(self, url: str, detail: str):
        detail = detail.strip()
        self.status = int(match.group(1)) if (match := _YT_DLP_STATUS.search(detail)) else None
        super().__init__(f"yt-dlp failed for {url}: {detail[-500:]}")


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
        raise ExtractorError(url, stderr.decode())
    produced = [line.strip() for line in stdout.decode().splitlines() if line.strip()]
    if not produced:
        raise ExtractorError(url, "produced no output file")
    return Path(produced[-1])


async def fetch_url(url: str, path: Path, timeout: float = 60.0) -> None:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(str(url))
        response.raise_for_status()
        path.write_bytes(response.content)
        logger.debug(f"Downloaded {url} -> {path}")


def is_transient(error: Exception) -> bool:
    if isinstance(error, ExtractorError):
        return error.status in _TRANSIENT_STATUS
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in _TRANSIENT_STATUS
    return isinstance(error, httpx.TransportError)


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
            if attempt == max_retries or not is_transient(e):
                logger.error(f"Failed to download {item_label} after {attempt} attempt(s): {e}")
                raise
            backoff = backoff_base * (2 ** (attempt - 1))
            logger.warning(f"Download attempt {attempt} failed for {item_label}, retrying in {backoff}s: {e}")
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Failed to download {item_label}")
