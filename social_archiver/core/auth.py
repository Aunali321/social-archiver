"""Credentials for the web UI and the MCP endpoint: one owner, any number of OAuth clients.

The owner signs in with WEB_PASSWORD and holds a session. MCP hosts register themselves and
receive access and refresh tokens once the owner approves them. Only token hashes are stored,
so a copy of the database grants nothing.
"""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import aiosqlite
from mcp.shared.auth import OAuthClientInformationFull

from social_archiver.core import migrations

MIGRATIONS = (
    (
        """
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            info TEXT NOT NULL,
            registered_at INTEGER NOT NULL
        )
        """,
        # grant_id ties an access token to the refresh token issued with it, so rotating or
        # revoking one takes the other along
        """
        CREATE TABLE IF NOT EXISTS tokens (
            hash TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            client_id TEXT,
            scopes TEXT NOT NULL,
            grant_id TEXT,
            expires_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tokens_grant ON tokens(grant_id)",
    ),
)


class TokenKind(StrEnum):
    SESSION = "session"
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(slots=True)
class StoredToken:
    kind: TokenKind
    client_id: str | None
    scopes: list[str]
    grant_id: str | None
    expires_at: int


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection = None  # type: ignore

    async def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await migrations.apply(self._connection, MIGRATIONS, "auth", self.db_path)
        await self._connection.execute("DELETE FROM tokens WHERE expires_at < ?", (int(time.time()),))
        await self._connection.commit()

    async def close(self):
        if self._connection:
            await self._connection.close()

    async def register_client(self, client: OAuthClientInformationFull):
        await self._connection.execute(
            "INSERT OR REPLACE INTO clients (client_id, info, registered_at) VALUES (?, ?, ?)",
            (client.client_id, client.model_dump_json(), int(time.time())),
        )
        await self._connection.commit()

    async def client(self, client_id: str) -> OAuthClientInformationFull | None:
        rows = await self._connection.execute_fetchall("SELECT info FROM clients WHERE client_id = ?", (client_id,))
        return OAuthClientInformationFull.model_validate_json(rows[0]["info"]) if rows else None

    async def issue(
        self,
        kind: TokenKind,
        ttl: int,
        client_id: str | None = None,
        scopes: list[str] | None = None,
        grant_id: str | None = None,
    ) -> tuple[str, int]:
        """A fresh random token and its expiry. The raw value is returned once, never stored."""
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + ttl
        await self._connection.execute(
            "INSERT INTO tokens (hash, kind, client_id, scopes, grant_id, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_hash(token), kind, client_id, json.dumps(scopes or []), grant_id, expires_at),
        )
        await self._connection.commit()
        return token, expires_at

    async def lookup(self, token: str, kind: TokenKind) -> StoredToken | None:
        rows = await self._connection.execute_fetchall(
            "SELECT * FROM tokens WHERE hash = ? AND kind = ? AND expires_at >= ?",
            (_hash(token), kind, int(time.time())),
        )
        if not rows:
            return None
        row = rows[0]
        return StoredToken(kind, row["client_id"], json.loads(row["scopes"]), row["grant_id"], row["expires_at"])

    async def revoke(self, token: str):
        await self._connection.execute("DELETE FROM tokens WHERE hash = ?", (_hash(token),))
        await self._connection.commit()

    async def revoke_grant(self, grant_id: str):
        await self._connection.execute("DELETE FROM tokens WHERE grant_id = ?", (grant_id,))
        await self._connection.commit()
