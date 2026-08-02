import os

from social_archiver.core.config import (
    CHECK_INTERVAL_MINUTES,
    DATA_DIR,
    DOWNLOADS_DIR,
    EMBEDDING_ENABLED,
    LOGS_DIR,
    TELEGRAM_BOT_TOKEN,
    VLM_PROVIDER,
    ConfigError,
    require,
)

# Phone backup to seed the archive from: a directory holding an Android msgstore.db (or its
# .crypt15, decrypted with the key below) plus the WhatsApp Media folder, and/or an iOS device
# backup with ChatStorage.sqlite inside. Whatever is found there is ingested.
WHATSAPP_EXPORT_DIR = os.getenv("WHATSAPP_EXPORT_DIR")
# The 64-digit end-to-end backup key from the phone's chat-backup settings; needed only when
# the export holds a .crypt15 rather than a plain msgstore.db
WHATSAPP_BACKUP_KEY = os.getenv("WHATSAPP_BACKUP_KEY")

# Store of the live bridge (wabridge/): session keys, message mirror, media. Archiver-owned
# state like the rest of DATA_DIR, not configuration — pairing in the web UI is the switch
# that turns the bridge on. The bridge holds view-once and since-deleted messages, which no
# phone backup ever contains.
BRIDGE_DIR = DATA_DIR / "wabridge"

TELEGRAM_CHAT_DM = int(os.getenv("WHATSAPP_CHAT_DM", "0"))

# Paths
DATABASE_PATH = DATA_DIR / "whatsapp.db"
WHATSAPP_MILVUS_URI = os.getenv("WHATSAPP_MILVUS_URI", str(DATA_DIR / "whatsapp_embeddings.db"))
LOG_FILE = LOGS_DIR / "whatsapp.log"


def validate_archive():
    if not (WHATSAPP_EXPORT_DIR or (BRIDGE_DIR / "wabridge.db").exists()):
        raise ConfigError(
            "Nothing to archive: set WHATSAPP_EXPORT_DIR to a phone backup and/or pair the bridge from the web UI"
        )


def validate_upload():
    require(TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN)
    if not TELEGRAM_CHAT_DM:
        raise ConfigError("Set WHATSAPP_CHAT_DM to enable uploading")


def validate_embed():
    if not EMBEDDING_ENABLED:
        raise ConfigError("EMBEDDING_ENABLED is false; enable it to use the embed job")
