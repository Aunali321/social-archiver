"""Twitter's presentation and layout choices, delegated to by the shared jobs."""

from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import Database, Item
from social_archiver.core.jobs import ensure_media
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

    def embed_thread_key(self, item: Item) -> str:
        return item.thread_root_id or item.item_id

    def embed_category(self, item: Item, loop_category: str) -> str:
        return loop_category

    async def embed_collect_media(self, db: Database, item: Item) -> list[Path]:
        return await ensure_media(db, self, item)

    async def embed_extra_context(self, db: Database, members: list[Item]) -> list[Item]:
        """Quoted tweets, which usually live in a different conversation and so
        are never reached by the thread walk — the single largest source of
        context, and the reason a caption can name what a tweet is replying to."""
        member_ids = {member.item_id for member in members}
        quoted_ids = {m.quoted_tweet_id for m in members if m.quoted_tweet_id} - member_ids
        return await db.items_by_ids(PLATFORM, quoted_ids)

    def embed_label(self, item: Item, context: dict[str, Item]) -> str:
        label = f"[tweet_id:{item.item_id}]"
        if item.origin and item.origin not in SEED_ORIGINS:
            label += f" [{item.origin}]"
        label += f" @{item.author_username}"
        if text := (item.text or "").strip():
            label += f": {text}"

        quoted = context.get(item.quoted_tweet_id) if item.quoted_tweet_id else None
        if quoted and (quoted_text := (quoted.text or "").strip()):
            label += f"\n  ↳ Quotes @{quoted.author_username}: {quoted_text}"
        return label
