"""WhatsApp's presentation and layout choices, delegated to by the shared jobs."""

from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import Item
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
