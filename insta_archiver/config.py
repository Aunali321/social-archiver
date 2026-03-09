import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Instagram Configuration
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
INSTAGRAM_DM_USERNAME = os.getenv("INSTAGRAM_DM_USERNAME")
INSTAGRAM_SESSIONID = os.getenv("INSTAGRAM_SESSIONID")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_LIKES = int(os.getenv("TELEGRAM_CHAT_LIKES", "-5254830835"))
TELEGRAM_CHAT_SAVED = int(os.getenv("TELEGRAM_CHAT_SAVED", "-5214899548"))
TELEGRAM_CHAT_SHARED = int(os.getenv("TELEGRAM_CHAT_SHARED", "-4942383347"))
TELEGRAM_CHAT_ERRORS = int(os.getenv("TELEGRAM_CHAT_ERRORS", "851091169"))

# Telegram file size limit (in MB)
# Default: 50 MB (official Bot API limit)
# Set higher values when using self-hosted Telegram Bot API server
TELEGRAM_MAX_FILE_SIZE_MB = int(os.getenv("TELEGRAM_MAX_FILE_SIZE_MB", "50"))

# Optional: Self-hosted Telegram Bot API server URL
# Example: http://localhost:8081
# Leave empty to use official Telegram API
# See: https://github.com/tdlib/telegram-bot-api
TELEGRAM_BOT_API_URL = os.getenv("TELEGRAM_BOT_API_URL", "")

# Behavior Configuration
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
FETCH_BATCH_SIZE = int(os.getenv("FETCH_BATCH_SIZE", "200"))
CLEANUP_DOWNLOADS = os.getenv("CLEANUP_DOWNLOADS", "true").lower() == "true"

# Embedding Configuration (OpenRouter or Gemini for VLM, local for embeddings)
EMBEDDING_ENABLED = os.getenv("EMBEDDING_ENABLED", "false").lower() == "true"
VLM_PROVIDER = os.getenv("VLM_PROVIDER", "openrouter")  # "openrouter" or "gemini"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.5-flash-lite")  # OpenRouter model
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")  # Direct Gemini model
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "300"))
INSTAGRAM_MILVUS_URI = os.getenv("INSTAGRAM_MILVUS_URI", "./milvus_instagram.db")

# Search Configuration
SEARCH_HYBRID_TOPK = int(os.getenv("SEARCH_HYBRID_TOPK", "50"))
SEARCH_RRF_K = int(os.getenv("SEARCH_RRF_K", "60"))
SEARCH_RERANK_TOPN = int(os.getenv("SEARCH_RERANK_TOPN", "10"))

# Paths
BASE_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_LIKES = DOWNLOADS_DIR / "likes"
DOWNLOADS_SAVED = DOWNLOADS_DIR / "saved"
DOWNLOADS_SHARED = DOWNLOADS_DIR / "shared"
LOGS_DIR = BASE_DIR / "logs"
DATABASE_PATH = BASE_DIR / "database.db"
SESSION_PATH = BASE_DIR / "session.json"

# Instagram API Configuration
INSTAGRAM_DELAY_RANGE = [2, 5]  # seconds between requests

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
LOG_FILE = LOGS_DIR / "insta_archiver.log"
LOG_ROTATION_DAYS = 30


def validate_config():
    """Validate required environment variables are set."""
    required_vars = {
        "INSTAGRAM_USERNAME": INSTAGRAM_USERNAME,
        "INSTAGRAM_PASSWORD": INSTAGRAM_PASSWORD,
        "INSTAGRAM_DM_USERNAME": INSTAGRAM_DM_USERNAME,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    }

    missing = [key for key, value in required_vars.items() if not value]

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    # Create necessary directories
    DOWNLOADS_LIKES.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_SAVED.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_SHARED.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
