"""Reading an iOS device backup: ChatStorage.sqlite and the media behind it.

Two layouts are accepted. A raw Finder/iTunes backup stores every file under a hashed name,
`{backup}/{fileID[:2]}/{fileID}`, with `Manifest.db` mapping domain and relative path to
fileID — ChatStorage.sqlite itself sits at a well-known hash, and each media file is looked
up through the manifest. An extracted tree (ChatStorage.sqlite beside a `Message/` folder)
is read directly. An encrypted backup is refused with instructions rather than guessed at.

Timestamps are Core Data seconds, offset from the Apple epoch. System rows (`ZMESSAGETYPE`
6) and revoked rows (14) are skipped for the same reasons as on Android.
"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

from social_archiver.core.config import ConfigError
from social_archiver.platforms.whatsapp.simple_message import WhatsAppMedia, WhatsAppMessage

logger = logging.getLogger(__name__)

BATCH = 1000

_APPLE_EPOCH = 978307200  # 2001-01-01 in Unix seconds, the Core Data zero
_STICKER = 15
_REVOKED = 14
_SYSTEM = 6
_QUOTE_MARKER = b"\x2a\x14"  # protobuf field 5, length 20: a quoted stanza id follows
_QUOTE_ID_LENGTH = 17  # the reply blob stores only the first 17 characters of the stanza id

_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
_CHATSTORAGE_FILE_ID = "7c7fba66680ef796b916b067077cc246adacf01d"

_QUERY = """
    SELECT
        ZWACHATSESSION.ZCONTACTJID AS chat_jid,
        ZWACHATSESSION.ZPARTNERNAME AS chat_name,
        ZWAMESSAGE.ZSTANZAID AS stanza_id,
        ZWAMESSAGE.ZISFROMME AS from_me,
        ZWAMESSAGE.ZMESSAGEDATE AS message_date,
        ZWAMESSAGE.ZTEXT AS text,
        ZWAMESSAGE.ZMESSAGETYPE AS message_type,
        ZWAMESSAGE.ZMETADATA AS metadata,
        ZWAGROUPMEMBER.ZMEMBERJID AS member_jid,
        ZWAMEDIAITEM.Z_PK AS media_pk,
        ZWAMEDIAITEM.ZMEDIALOCALPATH AS media_path,
        ZWAMEDIAITEM.ZVCARDSTRING AS media_mime,
        ZWAMEDIAITEM.ZTITLE AS media_caption
    FROM ZWAMESSAGE
        LEFT JOIN ZWAGROUPMEMBER ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        LEFT JOIN ZWAMEDIAITEM ON ZWAMEDIAITEM.ZMESSAGE = ZWAMESSAGE.Z_PK
        INNER JOIN ZWACHATSESSION ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
    ORDER BY ZWAMESSAGE.Z_PK
"""

_STANZAS = """
    SELECT ZWACHATSESSION.ZCONTACTJID AS chat_jid, ZWAMESSAGE.ZSTANZAID AS stanza_id
    FROM ZWAMESSAGE
        INNER JOIN ZWACHATSESSION ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
    WHERE ZWAMESSAGE.ZSTANZAID IS NOT NULL
"""

_NAMES = """
    SELECT ZWACHATSESSION.ZCONTACTJID AS jid, ZWACHATSESSION.ZPARTNERNAME AS partner,
           ZWAPROFILEPUSHNAME.ZPUSHNAME AS push
    FROM ZWACHATSESSION
        LEFT JOIN ZWAPROFILEPUSHNAME ON ZWACHATSESSION.ZCONTACTJID = ZWAPROFILEPUSHNAME.ZJID
"""


class IOSStore:
    name = "ios backup"

    def __init__(self, database: Path, folder: Path, manifest: Path | None):
        self.database = database
        self.folder = folder
        self.manifest = manifest

    @classmethod
    def find(cls, folder: Path) -> Self | None:
        extracted = folder / "ChatStorage.sqlite"
        if extracted.exists():
            return cls(extracted, folder, manifest=None)

        manifest = folder / "Manifest.db"
        if not manifest.exists():
            return None
        _require_unencrypted(manifest)
        hashed = folder / _CHATSTORAGE_FILE_ID[:2] / _CHATSTORAGE_FILE_ID
        if not hashed.exists():
            raise ConfigError(f"{folder} is a device backup, but WhatsApp's ChatStorage.sqlite is not in it")
        return cls(hashed, folder, manifest)

    async def walk(self) -> AsyncIterator[list[WhatsAppMessage]]:
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        manifest = _open_manifest(self.manifest)
        try:
            names = _names(connection)
            stanzas = _stanzas(connection)
            skipped = 0
            cursor = connection.execute(_QUERY)
            while rows := cursor.fetchmany(BATCH):
                batch = []
                for row in rows:
                    if row["stanza_id"] is None:
                        skipped += 1
                        continue
                    if (message := self._message(row, names, stanzas, manifest)) is not None:
                        batch.append(message)
                yield batch
            if skipped:
                logger.info(f"Skipped {skipped} rows without a stanza id, which nothing can dedupe against")
        finally:
            if manifest is not None:
                manifest.close()
            connection.close()

    def _message(
        self,
        row: sqlite3.Row,
        names: dict[str, str],
        stanzas: dict[tuple[str, str], str],
        manifest: sqlite3.Connection | None,
    ) -> WhatsAppMessage | None:
        chat_jid = row["chat_jid"]
        if not chat_jid or not chat_jid.endswith(("@s.whatsapp.net", "@g.us")):
            return None  # status broadcasts and channels
        if row["message_type"] in (_SYSTEM, _REVOKED):
            return None

        group = chat_jid.endswith("@g.us")
        sender_jid = row["member_jid"] if group else (None if row["from_me"] else chat_jid)
        return WhatsAppMessage(
            chat_jid=chat_jid,
            msg_id=row["stanza_id"],
            kind="group" if group else "dm",
            sender="me" if row["from_me"] else _display(sender_jid, names),
            sender_jid=None if row["from_me"] else sender_jid,
            # For a group the partner name is the group subject; for a DM it can be the bare
            # number, so the contact goes through the same preference the sender does
            chat_name=row["chat_name"] if group else _display(chat_jid, names),
            text=row["text"] or row["media_caption"],
            created_at=_when(row["message_date"]),
            quoted_msg_id=_quoted(chat_jid, row["metadata"], stanzas),
            from_me=bool(row["from_me"]),
            media=self._media(row, manifest),
        )

    def _media(self, row: sqlite3.Row, manifest: sqlite3.Connection | None) -> list[WhatsAppMedia]:
        if row["media_pk"] is None:
            return []
        kind = _kind(row["message_type"], row["media_mime"])
        local = row["media_path"]
        if local is None:
            return [WhatsAppMedia(kind, None)]

        if manifest is not None:
            path = _from_manifest(manifest, self.folder, local)
            return [WhatsAppMedia(kind, path, suffix=Path(local).suffix)]
        for root in (self.folder, self.folder / _DOMAIN):
            path = root / "Message" / local
            if path.exists():
                return [WhatsAppMedia(kind, path)]
        return [WhatsAppMedia(kind, None)]


def _require_unencrypted(manifest: Path):
    connection = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
    try:
        connection.execute("SELECT count(*) FROM Files").fetchone()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        raise ConfigError(
            f"{manifest.parent} is an encrypted device backup; back up again with encryption off, or decrypt it first"
        )
    finally:
        connection.close()


def _open_manifest(manifest: Path | None) -> sqlite3.Connection | None:
    if manifest is None:
        return None
    return sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)


def _from_manifest(manifest: sqlite3.Connection, folder: Path, local: str) -> Path | None:
    cursor = manifest.execute(
        "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?", (_DOMAIN, f"Message/{local}")
    )
    row = cursor.fetchone()
    if row is None:
        return None
    path = folder / row[0][:2] / row[0]
    return path if path.exists() else None


def _names(connection: sqlite3.Connection) -> dict[str, str]:
    """jid → display name. The partner name wins unless it is just the phone number and a
    push name exists — the same preference the phone's own UI applies."""
    names: dict[str, str] = {}
    for row in connection.execute(_NAMES):
        partner, push = row["partner"], row["push"]
        numeric = partner is not None and all(c in "+0123456789 " for c in partner)
        name = push if (not partner or numeric) and push else partner
        if row["jid"] and name:
            names[row["jid"]] = name
    return names


def _stanzas(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(chat, truncated stanza id) → full stanza id. The reply blob stores only 17 characters,
    and item ids carry the full id, so quotes resolve through this map."""
    return {
        (row["chat_jid"], row["stanza_id"][:_QUOTE_ID_LENGTH]): row["stanza_id"] for row in connection.execute(_STANZAS)
    }


def _display(jid: str | None, names: dict[str, str]) -> str:
    if jid is None:
        return "unknown"
    return names.get(jid) or jid.partition("@")[0]


def _quoted(chat_jid: str, metadata: bytes | None, stanzas: dict[tuple[str, str], str]) -> str | None:
    if not metadata or not metadata.startswith(_QUOTE_MARKER):
        return None
    prefix = metadata[2 : 2 + _QUOTE_ID_LENGTH].decode("ascii", errors="ignore")
    return stanzas.get((chat_jid, prefix))


def _when(seconds: float | None) -> datetime | None:
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds + _APPLE_EPOCH, tz=timezone.utc)


def _kind(message_type: int | None, mime: str | None) -> str:
    if message_type == _STICKER:
        return "sticker"
    prefix = (mime or "").partition("/")[0]
    return prefix if prefix in ("image", "video", "audio") else "document"
