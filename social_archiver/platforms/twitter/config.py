import os

from social_archiver.core.config import (
    BACKOFF_DELAYS,
    CHECK_INTERVAL_MINUTES,
    CLEANUP_DOWNLOADS,
    DATA_DIR,
    DOWNLOADS_DIR,
    EMBEDDING_ENABLED,
    EMBEDDING_TIMEOUT,
    FETCH_BATCH_SIZE,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LOG_LEVEL,
    LOG_ROTATION_DAYS,
    LOGS_DIR,
    MAX_DOWNLOAD_RETRIES,
    OPENROUTER_API_KEY,
    RETRY_BACKOFF_BASE,
    SEARCH_HYBRID_TOPK,
    SEARCH_RERANK_TOPN,
    SEARCH_RRF_K,
    TELEGRAM_BOT_API_URL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ERRORS,
    TELEGRAM_MAX_FILE_SIZE_MB,
    VERTEX_LOCATION,
    VERTEX_MODEL,
    VERTEX_PROJECT,
    VLM_MODEL,
    VLM_PROVIDER,
)

# Twitter auth (cookie-based)
TWITTER_AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN")
TWITTER_CT0 = os.getenv("TWITTER_CT0")
TWITTER_API_BASE = "https://x.com/i/api/graphql"
TWITTER_BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
TWITTER_DELAY_RANGE = [2, 5]

# Telegram channels
TELEGRAM_CHAT_LIKES = int(os.getenv("TWITTER_CHAT_LIKES", "0"))
TELEGRAM_CHAT_BOOKMARKS = int(os.getenv("TWITTER_CHAT_BOOKMARKS", "0"))

# Paths
DOWNLOADS_LIKES = DOWNLOADS_DIR / "twitter" / "likes"
DOWNLOADS_BOOKMARKS = DOWNLOADS_DIR / "twitter" / "bookmarks"
DATABASE_PATH = DATA_DIR / "twitter.db"
TWITTER_MILVUS_URI = os.getenv("TWITTER_MILVUS_URI", str(DATA_DIR / "twitter_embeddings.db"))
LOG_FILE = LOGS_DIR / "twitter.log"


def validate_config():
    required = {
        "TWITTER_AUTH_TOKEN": TWITTER_AUTH_TOKEN,
        "TWITTER_CT0": TWITTER_CT0,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    if not TELEGRAM_CHAT_BOOKMARKS and not TELEGRAM_CHAT_LIKES:
        raise ValueError("At least one of TWITTER_CHAT_BOOKMARKS or TWITTER_CHAT_LIKES must be set")

    DOWNLOADS_LIKES.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_BOOKMARKS.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
