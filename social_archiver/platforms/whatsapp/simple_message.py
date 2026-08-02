"""Simplified WhatsApp data model. Every message becomes one `WhatsAppMessage`,
whichever store it was read from: an Android backup, an iOS backup, or the live bridge."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import ArchiveStatus, Item

PLATFORM = "whatsapp"


@dataclass(slots=True)
class WhatsAppMedia:
    kind: str  # image, video, audio, document, sticker
    path: Path | None  # where the store holds the file; None when it was never downloaded or is gone
    suffix: str = ""  # extension when the path itself has none, as in a hashed iOS backup


@dataclass(slots=True)
class WhatsAppMessage:
    chat_jid: str
    msg_id: str
    kind: str  # "dm" | "group", which is also the archive category
    sender: str  # display name: contact or push name, falling back to the phone number
    sender_jid: str | None = None  # None for own messages; the store has no jid row for "me"
    chat_name: str | None = None
    text: str | None = None
    created_at: datetime | None = None
    quoted_msg_id: str | None = None
    from_me: bool = False
    media: list[WhatsAppMedia] = field(default_factory=list)

    @property
    def item_id(self) -> str:
        # Message ids are unique only within their chat, and one archive file holds every chat
        return f"{self.chat_jid}:{self.msg_id}"

    @property
    def thread_root_id(self) -> str:
        """A chat is one endless thread, far past what a VLM call can hold, so embedding
        groups it by UTC day instead. The root is synthetic — `thread_members` matches it
        against the column, never needing a row by that id — and deterministic, so the
        backup and the bridge assign a message the same group without seeing each other."""
        if self.created_at is None:
            return self.chat_jid
        return f"{self.chat_jid}:{self.created_at.astimezone(timezone.utc).date().isoformat()}"

    def to_item(self, category: str) -> Item:
        """Archived on insert: the stores are local, so the ingest links whatever media they
        still hold, and what they lost no download can recover. `media_urls` stays empty —
        WhatsApp media is encrypted on a CDN that expires it, not something a URL refetches —
        so a loss shows as media_count exceeding the files on disk."""
        return Item(
            item_id=self.item_id,
            platform=PLATFORM,
            category=category,
            author_username=self.sender,
            author_id=self.sender_jid,
            post_url=f"whatsapp://{self.chat_jid}/{self.msg_id}",
            text=self.text,
            created_at=self.created_at,
            has_media=bool(self.media),
            media_count=len(self.media),
            media_types=list({m.kind for m in self.media}),
            conversation_id=self.chat_jid,
            thread_root_id=self.thread_root_id,
            in_reply_to_status_id=f"{self.chat_jid}:{self.quoted_msg_id}" if self.quoted_msg_id else None,
            origin=category,
            chat_name=self.chat_name,
            archive_status=ArchiveStatus.ARCHIVED,
        )
