"""Reddit's presentation and layout choices, delegated to by the shared jobs."""

from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import Item
from social_archiver.platforms.reddit import config

PLATFORM = "reddit"

SEED_ORIGINS = frozenset({"saved", "upvoted", "downvoted", "own"})

_ORIGIN_LABELS = {"submission": "post", "parent": "parent"}

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


class RedditPort:
    platform = PLATFORM
    chats = {
        "saved": config.TELEGRAM_CHAT_SAVED,
        "upvoted": config.TELEGRAM_CHAT_UPVOTED,
        "downvoted": config.TELEGRAM_CHAT_DOWNVOTED,
        "own": config.TELEGRAM_CHAT_OWN,
    }

    def caption(self, item: Item) -> str:
        lines = [item.text] if item.text else []

        if item.origin and item.origin not in SEED_ORIGINS:
            lines.append(f"\n[{_ORIGIN_LABELS.get(item.origin, item.origin)}]")

        source = f"r/{item.subreddit} · u/{item.author_username}" if item.subreddit else f"u/{item.author_username}"
        lines.append(source)
        if item.like_count is not None:
            lines.append(f"{item.like_count} points")

        lines.append(item.post_url)
        if item.created_at:
            lines.append(item.created_at.strftime("%Y-%m-%d %H:%M:%S"))

        return "\n".join(lines)

    def downloads_folder(self, item: Item) -> Path:
        return config.DOWNLOADS_DIR / PLATFORM / item.category

    def upload_order(self, item: Item) -> tuple:
        """Seed items (the ones actually saved/voted/authored) go up first; their
        discovered thread context follows in chronological order."""
        return (item.origin not in SEED_ORIGINS, item.created_at or _EPOCH)
