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

# Instagram credentials
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
INSTAGRAM_DM_USERNAME = os.getenv("INSTAGRAM_DM_USERNAME")
INSTAGRAM_SESSIONID = os.getenv("INSTAGRAM_SESSIONID")
INSTAGRAM_DELAY_RANGE = [2, 5]

# Telegram channels
TELEGRAM_CHAT_LIKES = int(os.getenv("TELEGRAM_CHAT_LIKES", "0"))
TELEGRAM_CHAT_SAVED = int(os.getenv("TELEGRAM_CHAT_SAVED", "0"))
TELEGRAM_CHAT_SHARED = int(os.getenv("TELEGRAM_CHAT_SHARED", "0"))

# Paths
DOWNLOADS_LIKES = DOWNLOADS_DIR / "instagram" / "likes"
DOWNLOADS_SAVED = DOWNLOADS_DIR / "instagram" / "saved"
DOWNLOADS_SHARED = DOWNLOADS_DIR / "instagram" / "shared"
DATABASE_PATH = DATA_DIR / "instagram.db"
SESSION_PATH = DATA_DIR / "instagram_session.json"
INSTAGRAM_MILVUS_URI = os.getenv("INSTAGRAM_MILVUS_URI", str(DATA_DIR / "instagram_embeddings.db"))
LOG_FILE = LOGS_DIR / "instagram.log"


def validate_config():
    required = {
        "INSTAGRAM_USERNAME": INSTAGRAM_USERNAME,
        "INSTAGRAM_PASSWORD": INSTAGRAM_PASSWORD,
        "INSTAGRAM_DM_USERNAME": INSTAGRAM_DM_USERNAME,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    DOWNLOADS_LIKES.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_SAVED.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_SHARED.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
