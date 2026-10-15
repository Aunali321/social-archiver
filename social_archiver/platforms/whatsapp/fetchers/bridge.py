"""Reading the live bridge's mirror (wabridge/).

The bridge holds what no phone backup ever will: view-once media downloaded at receipt,
disappearing messages, and content that was deleted for everyone after it arrived — revoked
rows keep their text and files, flagged, and are ingested like any other message. Media was
already downloaded by the bridge into its own media/ folder, so rows point at local files.
"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

from social_archiver.core.profiles import ImageRef, ProfileSnapshot
from social_archiver.platforms.whatsapp.simple_message import (
    OWN_SENDER,
    WhatsAppMedia,
    WhatsAppMessage,
    author_snapshot,
)

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
        connection = self._connect()
        try:
            cursor = connection.execute(_QUERY)
            while rows := cursor.fetchmany(BATCH):
                yield [self._message(row) for row in rows]
        finally:
            connection.close()

    def account(self) -> str | None:
        """The paired account's jid; None until the bridge has connected once since recording it."""
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT jid FROM account").fetchone()
        return row["jid"] if row else None

    def profiles(self) -> list[ProfileSnapshot]:
        """What each account shows about itself, as the bridge last refreshed it."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT profiles.jid AS jid, contacts.name AS name, profiles.about AS about,
                       profiles.picture_id AS picture_id, profiles.picture_path AS picture_path
                FROM profiles LEFT JOIN contacts ON contacts.jid = profiles.jid
                """
            ).fetchall()
        return [self._profile(row) for row in rows]

    def _profile(self, row: sqlite3.Row) -> ProfileSnapshot:
        snapshot = author_snapshot(row["jid"], row["name"])
        snapshot.bio = row["about"]
        if row["picture_path"]:
            snapshot.avatar = ImageRef(row["picture_id"], Path(row["picture_path"]))
        return snapshot

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _message(self, row: sqlite3.Row) -> WhatsAppMessage:
        chat_jid = row["chat_jid"]
        group = (row["chat_kind"] or ("group" if chat_jid.endswith("@g.us") else "dm")) == "group"
        return WhatsAppMessage(
            chat_jid=chat_jid,
            msg_id=row["msg_id"],
            kind="group" if group else "dm",
            sender=OWN_SENDER
            if row["from_me"]
            else (row["sender_name"] or (row["sender_jid"] or "").partition("@")[0]),
            sender_jid=None if row["from_me"] else (row["sender_jid"] or None),
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
