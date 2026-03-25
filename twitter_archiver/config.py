import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Twitter Authentication (cookie-based, like bird)
TWITTER_AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN")
TWITTER_CT0 = os.getenv("TWITTER_CT0")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_BOOKMARKS = int(os.getenv("TELEGRAM_CHAT_BOOKMARKS", "0"))
TELEGRAM_CHAT_LIKES = int(os.getenv("TELEGRAM_CHAT_LIKES", "0"))
TELEGRAM_CHAT_ERRORS = int(os.getenv("TELEGRAM_CHAT_ERRORS", "0"))

# Telegram file size limit (in MB)
TELEGRAM_MAX_FILE_SIZE_MB = int(os.getenv("TELEGRAM_MAX_FILE_SIZE_MB", "50"))

# Optional: Self-hosted Telegram Bot API server URL
TELEGRAM_BOT_API_URL = os.getenv("TELEGRAM_BOT_API_URL", "")

# Behavior Configuration
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
FETCH_BATCH_SIZE = int(os.getenv("FETCH_BATCH_SIZE", "200"))
CLEANUP_DOWNLOADS = os.getenv("CLEANUP_DOWNLOADS", "true").lower() == "true"

# Embedding Configuration
EMBEDDING_ENABLED = os.getenv("EMBEDDING_ENABLED", "false").lower() == "true"
VLM_PROVIDER = os.getenv("VLM_PROVIDER", "openrouter")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.5-flash-lite")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "300"))
TWITTER_MILVUS_URI = os.getenv("TWITTER_MILVUS_URI", "./milvus_twitter.db")

# Search Configuration
SEARCH_HYBRID_TOPK = int(os.getenv("SEARCH_HYBRID_TOPK", "50"))
SEARCH_RRF_K = int(os.getenv("SEARCH_RRF_K", "60"))
SEARCH_RERANK_TOPN = int(os.getenv("SEARCH_RERANK_TOPN", "10"))

# Paths
BASE_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads_twitter"
DOWNLOADS_BOOKMARKS = DOWNLOADS_DIR / "bookmarks"
DOWNLOADS_LIKES = DOWNLOADS_DIR / "likes"
LOGS_DIR = BASE_DIR / "logs"
DATABASE_PATH = BASE_DIR / "database_twitter.db"

# Twitter API Configuration
TWITTER_API_BASE = "https://x.com/i/api/graphql"
TWITTER_BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
TWITTER_DELAY_RANGE = [2, 5]  # seconds between requests

# Retry Configuration
MAX_DOWNLOAD_RETRIES = 3
RETRY_BACKOFF_BASE = 30  # seconds

# Rate Limiting Backoff (seconds)
BACKOFF_DELAYS = {
    1: 30,
    2: 120,
    3: 300,
    4: 900,
}

# Logging
LOG_LEVEL = "DEBUG"
LOG_FILE = LOGS_DIR / "twitter_archiver.log"
LOG_ROTATION_DAYS = 30


def validate_config():
    """Validate required environment variables are set."""
    required_vars = {
        "TWITTER_AUTH_TOKEN": TWITTER_AUTH_TOKEN,
        "TWITTER_CT0": TWITTER_CT0,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    }

    missing = [key for key, value in required_vars.items() if not value]

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    # Check that at least one chat ID is configured
    if not TELEGRAM_CHAT_BOOKMARKS and not TELEGRAM_CHAT_LIKES:
        raise ValueError(
            "At least one of TELEGRAM_CHAT_BOOKMARKS or TELEGRAM_CHAT_LIKES must be set"
        )

    # Create necessary directories
    DOWNLOADS_BOOKMARKS.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_LIKES.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
