"""Shapes the read side returns. Queries take an ItemFilters, list endpoints return a Page
whose cursor is opaque to callers: it encodes the sort boundary, not an offset, so a page
stays stable while the archive grows underneath it."""

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime

from social_archiver.core.database import Item

# The engine's per-platform seed sets (platforms/*/port.py), plus liked_reply: the expander
# files a reply found through the likes check under its own origin, but the user did like it.
SEED_ORIGINS = {
    "twitter": frozenset({"liked", "bookmarked", "liked_reply"}),
    "reddit": frozenset({"saved", "upvoted", "downvoted", "own", "subreddit"}),
}


def is_seed(item: Item) -> bool:
    """Whether the user acted on this item themselves, as opposed to the expander pulling it
    in as context. Platforms without an origin vocabulary only archive what the user did."""
    seeds = SEED_ORIGINS.get(item.platform)
    return seeds is None or item.origin is None or item.origin in seeds


@dataclass(slots=True)
class ItemFilters:
    """Every field is optional; unset means unfiltered. `platforms` empty means all."""

    platforms: tuple[str, ...] = ()
    category: str | None = None
    author: str | None = None
    subreddit: str | None = None
    chat: str | None = None  # conversation_id: a WhatsApp chat JID or a Reddit submission
    collection: str | None = None
    origin: str | None = None
    archive_status: str | None = None
    source_target: str | None = None
    has_media: bool | None = None
    seeds_only: bool = False  # only what the user acted on; adopted context hidden
    date_from: datetime | None = None
    date_to: datetime | None = None


@dataclass(slots=True)
class Page:
    items: list[Item]
    next_cursor: str | None


@dataclass(slots=True)
class SearchHit:
    item: Item
    snippet: str | None


@dataclass(slots=True)
class ChatSummary:
    chat_id: str
    name: str | None
    category: str
    message_count: int
    last_at: datetime | None
    last_author: str | None
    last_text: str | None


@dataclass(slots=True)
class AuthorCount:
    author: str
    items: int


@dataclass(slots=True)
class PlatformStats:
    platform: str
    total: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    archive: dict[str, int] = field(default_factory=dict)
    upload: dict[str, int] = field(default_factory=dict)
    embed: dict[str, int] = field(default_factory=dict)
    with_media: int = 0
    with_local_media: int = 0
    authors: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    by_month: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class Facets:
    """Distinct filter values with counts, for populating the UI's filter controls."""

    categories: dict[str, int] = field(default_factory=dict)
    origins: dict[str, int] = field(default_factory=dict)
    subreddits: dict[str, int] = field(default_factory=dict)
    collections: dict[str, int] = field(default_factory=dict)


def encode_cursor(created_at: datetime | None, item_id: str) -> str:
    payload = json.dumps({"k": created_at.isoformat() if created_at else None, "id": item_id})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str | None, str]:
    """Raises ValueError on garbage, which callers surface as a bad-request."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload["k"], payload["id"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"invalid cursor {cursor!r}") from e
