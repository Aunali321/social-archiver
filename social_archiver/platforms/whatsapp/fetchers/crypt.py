"""Decrypting an Android msgstore.db.crypt15.

WhatsApp encrypts the nightly local backup with a key derived from the 64-digit code its
end-to-end backup settings show once. The file is a one-byte header length, a protobuf header
carrying a 16-byte IV, AES-256-GCM ciphertext of a zlib stream, and a 32-byte footer whose
first half is the GCM tag. Older .crypt12/.crypt14 files are keyed by a file that only a
rooted phone gives up, so they are refused outright rather than half-supported.
"""

import hmac
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from social_archiver.core.config import ConfigError

# The framing around the ciphertext: header length byte + 1, IV at a fixed offset inside the
# header, tag-carrying footer at the end.
_IV = slice(8, 24)
_FOOTER = 32
_TAG = 16


def decrypt_msgstore(source: Path, key_hex: str, target: Path) -> Path:
    blob = source.read_bytes()
    ciphertext = blob[blob[0] + 2 : -_FOOTER] + blob[-_FOOTER : -_FOOTER + _TAG]
    plain = zlib.decompress(AESGCM(_main_key(key_hex)).decrypt(blob[_IV], ciphertext, None))
    if not plain.startswith(b"SQLite"):
        raise ValueError(f"{source.name} decrypted to something that is not a SQLite database")
    target.write_bytes(plain)
    return target


def _main_key(key_hex: str) -> bytes:
    try:
        key = bytes.fromhex("".join(key_hex.split()))
    except ValueError:
        raise ConfigError("WHATSAPP_BACKUP_KEY is not hex; use the 64-digit key from the chat-backup settings")
    if len(key) != 32:
        raise ConfigError(f"WHATSAPP_BACKUP_KEY is {len(key)} bytes decoded; the 64-digit key decodes to 32")
    intermediate = hmac.new(b"\x00" * 32, key, "sha256").digest()
    return hmac.new(intermediate, b"backup encryption\x01", "sha256").digest()
