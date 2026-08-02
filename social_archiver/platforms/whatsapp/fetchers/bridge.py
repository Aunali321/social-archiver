"""Reading the live bridge's mirror (wabridge/).

The bridge holds what no phone backup ever will: view-once media downloaded at receipt,
disappearing messages, and content that was deleted for everyone after it arrived — revoked
rows keep their text and files, flagged, and are ingested like any other message. Media was
already downloaded by the bridge into its own media/ folder, so rows point at local files.
"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

from social_archiver.platforms.whatsapp.simple_message import WhatsAppMedia, WhatsAppMessage

logger = logging.getLogger(__name__)

BATCH = 1000

# The bridge stores gifs separately because WhatsApp encrypts them as videos; the archive
# does not care about the playback hint.
_KINDS = {
    "image": "image",
    "video": "video",
    "gif": "video",
    "audio": "audio",
    "document": "document",
    "sticker": "sticker",
}

_QUERY = """
    SELECT
        messages.chat_jid AS chat_jid,
        messages.msg_id AS msg_id,
        messages.sender_jid AS sender_jid,
        messages.sender_name AS sender_name,
        messages.from_me AS from_me,
        messages.ts AS ts,
        messages.text AS text,
        messages.quoted_msg_id AS quoted_msg_id,
        messages.media_type AS media_type,
        messages.local_path AS local_path,
        chats.kind AS chat_kind,
        chats.name AS chat_name
    FROM messages
        LEFT JOIN chats ON chats.jid = messages.chat_jid
    ORDER BY messages.chat_jid, messages.ts
"""


class BridgeStore:
    name = "bridge mirror"

    def __init__(self, database: Path):
        self.database = database

    @classmethod
    def find(cls, folder: Path) -> Self | None:
        database = folder / "wabridge.db"
        return cls(database) if database.exists() else None

    async def walk(self) -> AsyncIterator[list[WhatsAppMessage]]:
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(_QUERY)
            while rows := cursor.fetchmany(BATCH):
                yield [self._message(row) for row in rows]
        finally:
            connection.close()

    def _message(self, row: sqlite3.Row) -> WhatsAppMessage:
        chat_jid = row["chat_jid"]
        group = (row["chat_kind"] or ("group" if chat_jid.endswith("@g.us") else "dm")) == "group"
        return WhatsAppMessage(
            chat_jid=chat_jid,
            msg_id=row["msg_id"],
            kind="group" if group else "dm",
            sender="me" if row["from_me"] else (row["sender_name"] or (row["sender_jid"] or "").partition("@")[0]),
            sender_jid=None if row["from_me"] else row["sender_jid"],
            chat_name=row["chat_name"],
            text=row["text"] or None,
            created_at=datetime.fromtimestamp(row["ts"], tz=timezone.utc) if row["ts"] else None,
            quoted_msg_id=row["quoted_msg_id"] or None,
            from_me=bool(row["from_me"]),
            media=self._media(row),
        )

    def _media(self, row: sqlite3.Row) -> list[WhatsAppMedia]:
        if not row["media_type"]:
            return []
        kind = _KINDS.get(row["media_type"], "document")
        path = Path(row["local_path"]) if row["local_path"] else None
        return [WhatsAppMedia(kind, path if path and path.exists() else None)]
