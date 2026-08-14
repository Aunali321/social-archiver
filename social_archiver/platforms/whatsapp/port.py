"""WhatsApp's presentation and layout choices, delegated to by the shared jobs."""

from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import Database, Item
from social_archiver.platforms.whatsapp import config

PLATFORM = "whatsapp"

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


class WhatsAppPort:
    platform = PLATFORM
    # Groups are archive-only: absent here they are neither uploaded nor embedded, the same
    # standing subreddit sources have on Reddit. They join once the VLM choice settles.
    chats = {"dm": config.TELEGRAM_CHAT_DM}

    def caption(self, item: Item) -> str:
        lines = [item.text] if item.text else []
        where = f"{item.author_username} · {item.chat_name}" if item.chat_name else item.author_username
        lines.append(where)
        if item.created_at:
            lines.append(item.created_at.strftime("%Y-%m-%d %H:%M:%S"))
        return "\n".join(lines)

    def downloads_folder(self, item: Item) -> Path:
        return config.DOWNLOADS_DIR / PLATFORM / item.category

    def upload_order(self, item: Item) -> tuple:
        return (item.created_at or _EPOCH,)

    def embed_thread_key(self, item: Item) -> str:
        return item.thread_root_id or item.item_id

    def embed_category(self, item: Item, loop_category: str) -> str:
        return item.category

    async def embed_collect_media(self, db: Database, item: Item) -> list[Path]:
        """Only what is still on disk. WhatsApp media lives encrypted on a CDN
        that expires it, so a missing file is a permanent loss, not a restorable
        one: the message is captioned on its text alone rather than failing."""
        return [path for path in item.local_paths if path.exists()]

    async def embed_extra_context(self, db: Database, members: list[Item]) -> list[Item]:
        return []

    def embed_label(self, item: Item, context: dict[str, Item]) -> str:
        label = f"[tweet_id:{item.item_id}] {item.author_username}"
        if item.chat_name and item.chat_name != item.author_username:
            label += f" in {item.chat_name}"
        if text := (item.text or "").strip():
            label += f": {text}"
        return label
