import os

from social_archiver.core.config import (
    DATA_DIR,
    DOWNLOADS_DIR,
    EMBEDDING_ENABLED,
    LOGS_DIR,
    TELEGRAM_BOT_TOKEN,
    VLM_PROVIDER,
    ConfigError,
    require,
)

# Reddit OAuth; authenticate with a refresh token or fall back to username/password
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_REFRESH_TOKEN = os.getenv("REDDIT_REFRESH_TOKEN")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", f"social-archiver by u/{REDDIT_USERNAME or 'unknown'}")

# Account data export (.zip or extracted dir); backfills history past the 1000-item cap
REDDIT_EXPORT_PATH = os.getenv("REDDIT_EXPORT_PATH")
REDDIT_EXPORT_BATCH = int(os.getenv("REDDIT_EXPORT_BATCH", "250"))

# Directory of Arctic Shift subreddit dumps, holding r_<subreddit>_posts.jsonl and
# r_<subreddit>_comments.jsonl. The API cannot serve a whole subreddit, so this is the only
# way to archive one.
REDDIT_DUMP_DIR = os.getenv("REDDIT_DUMP_DIR")

# Telegram channels; a category is enabled by configuring its channel
TELEGRAM_CHAT_SAVED = int(os.getenv("REDDIT_CHAT_SAVED", "0"))
TELEGRAM_CHAT_UPVOTED = int(os.getenv("REDDIT_CHAT_UPVOTED", "0"))
TELEGRAM_CHAT_DOWNVOTED = int(os.getenv("REDDIT_CHAT_DOWNVOTED", "0"))
TELEGRAM_CHAT_OWN = int(os.getenv("REDDIT_CHAT_OWN", "0"))

# Paths
DATABASE_PATH = DATA_DIR / "reddit.db"
REDDIT_MILVUS_URI = os.getenv("REDDIT_MILVUS_URI", str(DATA_DIR / "reddit_embeddings.db"))
LOG_FILE = LOGS_DIR / "reddit.log"


def _require_a_channel():
    if not (TELEGRAM_CHAT_SAVED or TELEGRAM_CHAT_UPVOTED or TELEGRAM_CHAT_DOWNVOTED or TELEGRAM_CHAT_OWN):
        raise ConfigError(
            "Set REDDIT_CHAT_SAVED, REDDIT_CHAT_UPVOTED, REDDIT_CHAT_DOWNVOTED, and/or REDDIT_CHAT_OWN "
            "to enable a category"
        )


def validate_archive():
    require(REDDIT_CLIENT_ID=REDDIT_CLIENT_ID)
    if not REDDIT_REFRESH_TOKEN:
        require(
            REDDIT_CLIENT_SECRET=REDDIT_CLIENT_SECRET,
            REDDIT_USERNAME=REDDIT_USERNAME,
            REDDIT_PASSWORD=REDDIT_PASSWORD,
        )


def validate_source():
    """No Reddit credentials: a dump is read off disk, and its media urls are public."""
    require(REDDIT_DUMP_DIR=REDDIT_DUMP_DIR)


def validate_upload():
    require(TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN)
    _require_a_channel()


def validate_embed():
    if not EMBEDDING_ENABLED:
        raise ConfigError("EMBEDDING_ENABLED is false; enable it to use the embed job")
