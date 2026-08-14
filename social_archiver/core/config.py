import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(ValueError):
    """Configuration problem the user must fix; reported without a traceback."""


def require(**values: str | None):
    """Raise unless every named value is set: require(SOME_TOKEN=SOME_TOKEN, ...)."""
    if missing := [name for name, value in values.items() if not value]:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")


# Telegram (shared bot, per-platform chat IDs live in each platform's config)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ERRORS = int(os.getenv("TELEGRAM_CHAT_ERRORS", "0")) or None
TELEGRAM_MAX_FILE_SIZE_MB = int(os.getenv("TELEGRAM_MAX_FILE_SIZE_MB", "50"))
TELEGRAM_BOT_API_URL = os.getenv("TELEGRAM_BOT_API_URL", "")

# Every platform this build serves; each owns a database, a worker, and a schedule row
PLATFORMS = ("instagram", "reddit", "twitter", "whatsapp")

# Behavior
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "240"))  # seeds new schedules; per-platform in the UI
# A restart should not trigger a fetch; the daemon waits for its first interval
RUN_ON_START = os.getenv("RUN_ON_START", "false").lower() == "true"
FETCH_BATCH_SIZE = int(os.getenv("FETCH_BATCH_SIZE", "200"))
CLEANUP_DOWNLOADS = os.getenv("CLEANUP_DOWNLOADS", "true").lower() == "true"

# Captioning / VLM
# Captioning (VLM descriptions + traces) and search indexing (embed into Milvus)
# are separate stages: caption first, index later. Each has its own enable flag
# so captioning can run with no embedding server, and indexing can wait for one.
CAPTIONING_ENABLED = os.getenv("CAPTIONING_ENABLED", "false").lower() == "true"
EMBEDDING_ENABLED = os.getenv("EMBEDDING_ENABLED", "false").lower() == "true"
VLM_PROVIDER = os.getenv("VLM_PROVIDER", "vertex")  # "openrouter", "gemini", or "vertex"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.5-flash-lite")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-3.1-pro-preview")
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
# Flex is half Standard's price for the same models, synchronously, so it is the
# default; Standard or Priority trade cost for latency guarantees this workload
# does not need.
VERTEX_SERVICE_TIER = os.getenv("VERTEX_SERVICE_TIER", "SERVICE_TIER_FLEX")
# Capture the model's reasoning trace alongside each caption. On, the traces form
# a distillation dataset (at the cost of the reasoning tokens); off is cheaper.
VLM_CAPTURE_REASONING = os.getenv("VLM_CAPTURE_REASONING", "true").lower() == "true"
VLM_MAX_OUTPUT_TOKENS = int(os.getenv("VLM_MAX_OUTPUT_TOKENS", "65536"))
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "900"))

# How many VLM calls run at once. Vertex serves well above this concurrency; the
# practical limit is transient 503s, which each call retries through.
EMBED_CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", "8"))
# Media items per call, bounding the output a single call must produce. A thread
# with more is split across calls, each carrying the full thread text.
EMBED_MAX_MEDIA_PER_CALL = int(os.getenv("EMBED_MAX_MEDIA_PER_CALL", "20"))
# A safety ceiling on context for pathological threads; real threads sit far
# under it, so it never bites normally.
VLM_MAX_CONTEXT_POSTS = int(os.getenv("VLM_MAX_CONTEXT_POSTS", "3000"))
# Conversations a caption run loads at once. A batch holds its posts and every
# thread member around them, so this is what bounds the run against a backlog of
# millions rather than the machine's memory.
CAPTION_BATCH_CONVERSATIONS = int(os.getenv("CAPTION_BATCH_CONVERSATIONS", "500"))
# Vertex accepts a 500 MB request payload and base64 inflates ~33%, so cap the
# raw file below that. Larger media is indexed on its text alone.
VLM_MAX_MEDIA_BYTES = int(os.getenv("VLM_MAX_MEDIA_BYTES", str(360 * 1024 * 1024)))

# Embedding and reranking run on a separate OpenAI-compatible server, so the archiver
# needs no ML runtime of its own. vLLM, llama.cpp and TEI all serve these shapes.
EMBED_URL = os.getenv("EMBED_URL", "http://localhost:8000/v1/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "jinaai/jina-embeddings-v5-omni-small")
# Asymmetric retrieval prefixes, which differ per model family
EMBED_QUERY_PREFIX = os.getenv("EMBED_QUERY_PREFIX", "Query: ")
EMBED_DOC_PREFIX = os.getenv("EMBED_DOC_PREFIX", "Document: ")
# Reranking is optional: unset RERANK_URL and search returns vector order
RERANK_URL = os.getenv("RERANK_URL", "")
RERANK_MODEL = os.getenv("RERANK_MODEL", "jinaai/jina-reranker-v3")

# Search
SEARCH_HYBRID_TOPK = int(os.getenv("SEARCH_HYBRID_TOPK", "50"))
SEARCH_RRF_K = int(os.getenv("SEARCH_RRF_K", "60"))
SEARCH_RERANK_TOPN = int(os.getenv("SEARCH_RERANK_TOPN", "10"))

# Paths. Overridable so a container can point them at mounted volumes instead of the
# repo layout, which is what keeps the archive outside the image.
BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR / "data")
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR") or BASE_DIR / "downloads")
LOGS_DIR = Path(os.getenv("LOGS_DIR") or BASE_DIR / "logs")

# Retry
MAX_DOWNLOAD_RETRIES = 3
RETRY_BACKOFF_BASE = 30  # seconds
# Video downloads are mostly yt-dlp waiting on the network, with a short ffmpeg mux at the
# end, so several at once cost little CPU. Kept low to share the machine.
DOWNLOAD_CONCURRENCY = int(os.getenv("DOWNLOAD_CONCURRENCY", "4"))

# Logging
LOG_ROTATION_DAYS = 30
