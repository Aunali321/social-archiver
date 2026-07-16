"""Instagram's presentation and layout choices, delegated to by the shared jobs."""
from datetime import datetime
from pathlib import Path

from social_archiver.core.database import Item
from social_archiver.platforms.instagram import config

PLATFORM = "instagram"


def _safe_dirname(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    return cleaned or "uncategorized"


class InstagramPort:
    platform = PLATFORM
    chats = {
        "likes": config.TELEGRAM_CHAT_LIKES,
        "saved": config.TELEGRAM_CHAT_SAVED,
        "shared": config.TELEGRAM_CHAT_SHARED,
    }

    def caption(self, item: Item) -> str:
        lines = [item.text] if item.text else []

        if item.collection_name:
            lines.append(f"\n📁 {item.collection_name}")
        lines.append(f"👤 @{item.author_username}")
        if item.shared_by_username:
            lines.append(f"📤 Shared by @{item.shared_by_username}")

        lines.append(f"🔗 {item.post_url}")
        if item.created_at:
            lines.append(f"📅 {item.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(lines)

    def downloads_folder(self, item: Item) -> Path:
        folder = config.DOWNLOADS_DIR / PLATFORM / item.category
        if item.category == "saved" and item.collection_name:
            folder /= _safe_dirname(item.collection_name)
        return folder

    def upload_order(self, item: Item) -> tuple:
        return (item.created_at or datetime.min,)
