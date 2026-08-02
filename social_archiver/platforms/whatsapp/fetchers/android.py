"""Reading an Android phone backup: msgstore.db and the WhatsApp Media folder.

Only the current schema (the `message` table, WhatsApp since 2019) is read; a legacy
`messages` database is refused loudly rather than half-read. Modern databases address some
chats and senders by LID rather than phone-number jid; when the `jid_map` table exists both
are resolved through it, so the same conversation never appears under two ids.

System rows (`status` 6) and revoked rows (`message_type` 15) are skipped: the first are
notices, not messages, and the second hold nothing — the phone deleted the content, and only
the live bridge can catch it beforehand.
"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

from social_archiver.core.config import ConfigError
from social_archiver.platforms.whatsapp.fetchers.crypt import decrypt_msgstore
from social_archiver.platforms.whatsapp.simple_message import WhatsAppMedia, WhatsAppMessage

logger = logging.getLogger(__name__)

BATCH = 1000

_STICKER = 20
_REVOKED = 15
_SYSTEM_STATUS = 6
_JID_PM, _JID_GROUP = 0, 1

_QUERY = """
    SELECT
        {chat_jid} AS chat_jid,
        message.key_id AS key_id,
        message.from_me AS from_me,
        message.timestamp AS timestamp,
        message.text_data AS text_data,
        message.message_type AS message_type,
        message.status AS status,
        {sender_jid} AS sender_jid,
        jid_global.type AS jid_type,
        chat.subject AS chat_subject,
        message_quoted.key_id AS quoted_id,
        message_media.file_path AS media_path,
        message_media.mime_type AS mime_type
    FROM message
        LEFT JOIN message_quoted ON message_quoted.message_row_id = message._id
        LEFT JOIN message_media ON message_media.message_row_id = message._id
        LEFT JOIN chat ON chat._id = message.chat_row_id
        INNER JOIN jid jid_global ON jid_global._id = chat.jid_row_id
        LEFT JOIN jid jid_group ON jid_group._id = message.sender_jid_row_id
        {jid_map_joins}
    ORDER BY message._id
"""

_JID_MAP_JOINS = """
        LEFT JOIN jid_map jid_map_global ON chat.jid_row_id = jid_map_global.lid_row_id
        LEFT JOIN jid lid_global ON jid_map_global.jid_row_id = lid_global._id
        LEFT JOIN jid_map jid_map_group ON message.sender_jid_row_id = jid_map_group.lid_row_id
        LEFT JOIN jid lid_group ON jid_map_group.jid_row_id = lid_group._id
"""


class AndroidStore:
    name = "android backup"

    def __init__(self, database: Path, folder: Path):
        self.database = database
        self.media_root = _media_root(folder)
        self.contacts = _contacts(folder / "wa.db")

    @classmethod
    def find(cls, folder: Path, backup_key: str | None) -> Self | None:
        plain = folder / "msgstore.db"
        if plain.exists():
            return cls(plain, folder)
        encrypted = next(iter(sorted(folder.glob("msgstore*.crypt15"))), None)
        if encrypted is None:
            if legacy := next(iter(folder.glob("msgstore*.crypt1[24]")), None):
                raise ConfigError(f"{legacy.name} is a crypt12/14 backup, which needs the phone's root-only key file")
            return None
        if not backup_key:
            raise ConfigError(f"{encrypted.name} needs WHATSAPP_BACKUP_KEY to decrypt")

        decrypted = folder / "msgstore.decrypted.db"
        if not decrypted.exists() or decrypted.stat().st_mtime < encrypted.stat().st_mtime:
            logger.info(f"Decrypting {encrypted.name} ({encrypted.stat().st_size / 1e6:,.0f} MB)")
            decrypt_msgstore(encrypted, backup_key, decrypted)
        return cls(decrypted, folder)

    async def walk(self) -> AsyncIterator[list[WhatsAppMessage]]:
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            _require_current_schema(connection)
            resolved = _has_table(connection, "jid_map")
            query = _QUERY.format(
                chat_jid="COALESCE(lid_global.raw_string, jid_global.raw_string)"
                if resolved
                else "jid_global.raw_string",
                sender_jid="COALESCE(lid_group.raw_string, jid_group.raw_string)"
                if resolved
                else "jid_group.raw_string",
                jid_map_joins=_JID_MAP_JOINS if resolved else "",
            )
            cursor = connection.execute(query)
            while rows := cursor.fetchmany(BATCH):
                yield [message for row in rows if (message := self._message(row)) is not None]
        finally:
            connection.close()

    def _message(self, row: sqlite3.Row) -> WhatsAppMessage | None:
        if row["jid_type"] not in (_JID_PM, _JID_GROUP):
            return None  # status, broadcast lists, newsletters
        if row["status"] == _SYSTEM_STATUS or row["message_type"] == _REVOKED:
            return None

        chat_jid = row["chat_jid"]
        group = row["jid_type"] == _JID_GROUP
        sender_jid = row["sender_jid"] if group else (None if row["from_me"] else chat_jid)
        return WhatsAppMessage(
            chat_jid=chat_jid,
            msg_id=row["key_id"],
            kind="group" if group else "dm",
            sender="me" if row["from_me"] else self._display(sender_jid),
            sender_jid=None if row["from_me"] else sender_jid,
            chat_name=row["chat_subject"] if group else self._display(chat_jid),
            text=row["text_data"],
            created_at=_when(row["timestamp"]),
            quoted_msg_id=row["quoted_id"],
            from_me=bool(row["from_me"]),
            media=self._media(row),
        )

    def _media(self, row: sqlite3.Row) -> list[WhatsAppMedia]:
        if row["media_path"] is None and row["mime_type"] is None:
            return []
        kind = _kind(row["message_type"], row["mime_type"])
        if row["media_path"] is None or self.media_root is None:
            return [WhatsAppMedia(kind, None)]
        path = self.media_root / row["media_path"]
        return [WhatsAppMedia(kind, path if path.exists() else None)]

    def _display(self, jid: str | None) -> str:
        if jid is None:
            return "unknown"
        return self.contacts.get(jid) or jid.partition("@")[0]


def _require_current_schema(connection: sqlite3.Connection):
    if not _has_table(connection, "message"):
        raise ConfigError("msgstore.db uses the legacy pre-2019 schema, which this reader does not support")


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    cursor = connection.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
    return cursor.fetchone()[0] > 0


def _media_root(folder: Path) -> Path | None:
    """`message_media.file_path` is relative to the WhatsApp app-data root, the folder that
    contains `Media/` — either the export folder itself or a `WhatsApp/` inside it."""
    for root in (folder, folder / "WhatsApp"):
        if (root / "Media").exists():
            return root
    logger.warning(f"No Media folder under {folder}; media is recorded but no files can be linked")
    return None


def _contacts(wa_db: Path) -> dict[str, str]:
    """jid → display name out of wa.db. Optional: without it senders fall back to numbers."""
    if not wa_db.exists():
        logger.warning("No wa.db beside msgstore.db; senders will show as phone numbers")
        return {}
    connection = sqlite3.connect(f"file:{wa_db}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            "SELECT jid, COALESCE(display_name, wa_name) AS name FROM wa_contacts WHERE jid IS NOT NULL"
        )
        return {jid: name for jid, name in cursor.fetchall() if name}
    finally:
        connection.close()


def _when(timestamp: int | None) -> datetime | None:
    """Milliseconds since the epoch in the current schema."""
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)


def _kind(message_type: int | None, mime_type: str | None) -> str:
    if message_type == _STICKER:
        return "sticker"
    prefix = (mime_type or "").partition("/")[0]
    return prefix if prefix in ("image", "video", "audio") else "document"
