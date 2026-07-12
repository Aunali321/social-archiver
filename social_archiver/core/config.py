import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram (shared bot, per-platform chat IDs live in each platform's config)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ERRORS = int(os.getenv("TELEGRAM_CHAT_ERRORS", "0")) or None
TELEGRAM_MAX_FILE_SIZE_MB = int(os.getenv("TELEGRAM_MAX_FILE_SIZE_MB", "50"))
TELEGRAM_BOT_API_URL = os.getenv("TELEGRAM_BOT_API_URL", "")

# Behavior
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
FETCH_BATCH_SIZE = int(os.getenv("FETCH_BATCH_SIZE", "200"))
CLEANUP_DOWNLOADS = os.getenv("CLEANUP_DOWNLOADS", "true").lower() == "true"

# Embedding / VLM
EMBEDDING_ENABLED = os.getenv("EMBEDDING_ENABLED", "false").lower() == "true"
VLM_PROVIDER = os.getenv("VLM_PROVIDER", "vertex")  # "openrouter", "gemini", or "vertex"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.5-flash-lite")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-3-flash-preview")
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "300"))

# Search
SEARCH_HYBRID_TOPK = int(os.getenv("SEARCH_HYBRID_TOPK", "50"))
SEARCH_RRF_K = int(os.getenv("SEARCH_RRF_K", "60"))
SEARCH_RERANK_TOPN = int(os.getenv("SEARCH_RERANK_TOPN", "10"))

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = BASE_DIR / "downloads"
LOGS_DIR = BASE_DIR / "logs"

# Retry
MAX_DOWNLOAD_RETRIES = 3
RETRY_BACKOFF_BASE = 30  # seconds
BACKOFF_DELAYS = {
    1: 30,
    2: 120,
    3: 300,
    4: 900,
}

# Logging
LOG_LEVEL = "DEBUG"
LOG_ROTATION_DAYS = 30
