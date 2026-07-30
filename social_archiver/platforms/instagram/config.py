import os

from social_archiver.core.config import (
    CHECK_INTERVAL_MINUTES,
    DATA_DIR,
    DOWNLOAD_CONCURRENCY,
    DOWNLOADS_DIR,
    EMBEDDING_ENABLED,
    FETCH_BATCH_SIZE,
    LOGS_DIR,
    TELEGRAM_BOT_TOKEN,
    VLM_PROVIDER,
    ConfigError,
    require,
)

# Instagram credentials
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
INSTAGRAM_DM_USERNAME = os.getenv("INSTAGRAM_DM_USERNAME")
INSTAGRAM_SESSIONID = os.getenv("INSTAGRAM_SESSIONID")
INSTAGRAM_DELAY_RANGE = [2, 5]
# (connect, read). instagrapi passes no timeout and requests has no default, so a host
# that accepts the connection then goes quiet blocks until the process is killed.
INSTAGRAM_REQUEST_TIMEOUT = (10, 30)

# Telegram channels; a category is enabled by configuring its channel
TELEGRAM_CHAT_LIKES = int(os.getenv("TELEGRAM_CHAT_LIKES", "0"))
TELEGRAM_CHAT_SAVED = int(os.getenv("TELEGRAM_CHAT_SAVED", "0"))
TELEGRAM_CHAT_SHARED = int(os.getenv("TELEGRAM_CHAT_SHARED", "0"))

# Paths
DATABASE_PATH = DATA_DIR / "instagram.db"
SESSION_PATH = DATA_DIR / "instagram_session.json"
INSTAGRAM_MILVUS_URI = os.getenv("INSTAGRAM_MILVUS_URI", str(DATA_DIR / "instagram_embeddings.db"))
LOG_FILE = LOGS_DIR / "instagram.log"


def _require_a_channel():
    if not (TELEGRAM_CHAT_LIKES or TELEGRAM_CHAT_SAVED or TELEGRAM_CHAT_SHARED):
        raise ConfigError(
            "Set TELEGRAM_CHAT_LIKES, TELEGRAM_CHAT_SAVED, and/or TELEGRAM_CHAT_SHARED to enable a category"
        )


def validate_archive():
    """Archiving is independent of Telegram: items are recorded and downloaded either way,
    and `upload` picks up whatever has a channel whenever one is configured."""
    require(
        INSTAGRAM_USERNAME=INSTAGRAM_USERNAME,
        INSTAGRAM_PASSWORD=INSTAGRAM_PASSWORD,
    )


def validate_upload():
    require(TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN)
    _require_a_channel()


def validate_embed():
    if not EMBEDDING_ENABLED:
        raise ConfigError("EMBEDDING_ENABLED is false; enable it to use the embed job")
