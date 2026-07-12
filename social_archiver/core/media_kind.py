from pathlib import Path
from typing import Literal

MediaKind = Literal["photo", "video"]

_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

_MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".webm": "video/webm",
}


def guess_media_kind(path: Path) -> MediaKind:
    return "video" if path.suffix.lower() in _VIDEO_SUFFIXES else "photo"


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_SUFFIXES


def guess_mime_type(path: Path) -> str | None:
    return _MIME_TYPES.get(path.suffix.lower())
