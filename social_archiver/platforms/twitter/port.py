"""Twitter's presentation and layout choices, delegated to by the shared jobs."""
from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import Item
from social_archiver.platforms.twitter import config

PLATFORM = "twitter"

SEED_ORIGINS = frozenset({"liked", "bookmarked"})

_ORIGIN_LABELS = {
    "thread": "thread",
    "parent": "parent",
    "quoted": "quoted",
    "linked": "linked",
    "liked_reply": "liked reply",
    "retweet": "retweet",
}

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


class TwitterPort:
    platform = PLATFORM
    chats = {"likes": config.TELEGRAM_CHAT_LIKES, "bookmarks": config.TELEGRAM_CHAT_BOOKMARKS}

    def caption(self, item: Item) -> str:
        lines = [item.text] if item.text else []

        if item.origin and item.origin not in SEED_ORIGINS:
            lines.append(f"\n[{_ORIGIN_LABELS.get(item.origin, item.origin)}]")

        lines.append(f"@{item.author_username}")

        stats = [
            f"{count} {noun}"
            for count, noun in ((item.like_count, "likes"), (item.retweet_count, "RTs"))
            if count is not None
        ]
        if stats:
            lines.append(" | ".join(stats))

        lines.append(item.post_url)
        if item.created_at:
            lines.append(item.created_at.strftime("%Y-%m-%d %H:%M:%S"))

        return "\n".join(lines)

    def downloads_folder(self, item: Item) -> Path:
        return config.DOWNLOADS_DIR / PLATFORM / item.category

    def upload_order(self, item: Item) -> tuple:
        """Seed tweets (the ones actually liked or bookmarked) go up first;
        their discovered context follows in chronological order."""
        return (item.origin not in SEED_ORIGINS, item.created_at or _EPOCH)
