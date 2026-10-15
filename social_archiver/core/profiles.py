"""The people behind archived items, one profile per account across every platform.

A profile is keyed by the platform's own account id, never by its handle: handles change and
are reused, ids are neither. Everything an account showed about itself is versioned, so a
rename, a new bio or a new avatar adds a version instead of overwriting the last one. Counts
move on every sighting and say nothing about who the account is, so they are kept current
on the profile instead of versioned.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class ProfileStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class ImageKind(StrEnum):
    AVATAR = "avatar"
    BANNER = "banner"


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A picture as a sighting names it. `key` identifies the picture itself, because the
    address it is served from can change while the picture does not: Instagram re-signs its
    links on every response. `source` is a URL to download, or a file another process has
    already fetched."""

    key: str
    source: str | Path


@dataclass(slots=True)
class ProfileSnapshot:
    """One sighting of an account. None means the sighting did not carry that field, never
    that the account has it empty: a post's payload says nothing about a bio, and must not
    read as the bio having been deleted."""

    profile_id: str
    handle: str
    display_name: str | None = None
    bio: str | None = None
    location: str | None = None
    website: str | None = None
    verified: bool | None = None
    private: bool | None = None
    avatar: ImageRef | None = None
    banner: ImageRef | None = None
    followers: int | None = None
    following: int | None = None
    posts: int | None = None
    joined_at: datetime | None = None
    status: ProfileStatus | None = None


# What a version is made of. A sighting that differs from the latest version in any of these,
# where both are known, is a new version; one that only fills in what was unknown is not.
VERSIONED = ("handle", "display_name", "bio", "location", "website", "verified", "private", "avatar_key", "banner_key")
