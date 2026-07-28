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

# Twitter auth (cookie-based)
TWITTER_AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN")
TWITTER_CT0 = os.getenv("TWITTER_CT0")
TWITTER_API_BASE = "https://x.com/i/api/graphql"
TWITTER_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
TWITTER_DELAY_RANGE = [2, 5]

# Official data export (.zip or extracted dir); like.js backfills full like history.
TWITTER_EXPORT_PATH = os.getenv("TWITTER_EXPORT_PATH")

# Seeds expanded before anything is committed. Expansion holds its results until it finishes, so
# this is what an interrupt costs, whether the seeds came from the timeline or the export.
TWITTER_EXPAND_BATCH = int(os.getenv("TWITTER_EXPAND_BATCH", "250"))

# Telegram channels; a category is enabled by configuring its channel
TELEGRAM_CHAT_LIKES = int(os.getenv("TWITTER_CHAT_LIKES", "0"))
TELEGRAM_CHAT_BOOKMARKS = int(os.getenv("TWITTER_CHAT_BOOKMARKS", "0"))

# Paths
DATABASE_PATH = DATA_DIR / "twitter.db"
TWITTER_MILVUS_URI = os.getenv("TWITTER_MILVUS_URI", str(DATA_DIR / "twitter_embeddings.db"))
LOG_FILE = LOGS_DIR / "twitter.log"


def _require_a_channel():
    if not (TELEGRAM_CHAT_LIKES or TELEGRAM_CHAT_BOOKMARKS):
        raise ConfigError("Set TWITTER_CHAT_LIKES and/or TWITTER_CHAT_BOOKMARKS to enable a category")


def validate_archive():
    """Archiving is independent of Telegram: items are recorded and downloaded either way,
    and `upload` picks up whatever has a channel whenever one is configured."""
    require(TWITTER_AUTH_TOKEN=TWITTER_AUTH_TOKEN, TWITTER_CT0=TWITTER_CT0)


def validate_upload():
    require(TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN)
    _require_a_channel()


def validate_embed():
    if not EMBEDDING_ENABLED:
        raise ConfigError("EMBEDDING_ENABLED is false; enable it to use the embed job")
