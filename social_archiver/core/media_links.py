"""Signed, expiring links to archived media files, for clients that cannot hold a session.

An MCP host such as claude.ai fetches media on its own servers, without the owner's cookie,
so a link carries its own proof: an HMAC over the file's address and an expiry. The key is
random and kept beside the archive, so links survive a restart and nothing else signs them.
"""

import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from social_archiver.core import config

TTL = 24 * 3600


class MediaLinks:
    def __init__(self, key_path: Path):
        self.key_path = key_path
        self._key: bytes | None = None

    def _signing_key(self) -> bytes:
        if self._key is None:
            try:
                with self.key_path.open("xb") as f:  # exclusive: two processes cannot both mint one
                    f.write(secrets.token_bytes(32))
                self.key_path.chmod(0o600)
            except FileExistsError:
                pass
            self._key = self.key_path.read_bytes()
        return self._key

    def _signature(self, platform: str, item_id: str, index: int, expires: int) -> str:
        message = f"{platform}\n{item_id}\n{index}\n{expires}".encode()
        digest = hmac.new(self._signing_key(), message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def url(self, platform: str, item_id: str, index: int) -> str:
        expires = int(time.time()) + TTL
        signature = self._signature(platform, item_id, index, expires)
        return (
            f"{config.PUBLIC_URL}/media/{platform}/{quote(item_id, safe='')}/{index}"
            f"?expires={expires}&signature={signature}"
        )

    def valid(self, platform: str, item_id: str, index: int, expires: int, signature: str) -> bool:
        if expires < time.time():
            return False
        return hmac.compare_digest(signature, self._signature(platform, item_id, index, expires))


links = MediaLinks(config.DATA_DIR / "media-links.key")
