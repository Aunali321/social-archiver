# Social Archiver

Automated archiver that pulls content you've liked/saved/bookmarked across social platforms and uploads it to Telegram, with optional semantic search over the archive.

Currently supports **Instagram** (likes, saved collections, DM shares) and **Twitter/X** (likes, with full thread/quote/retweet expansion). Built to add more platforms without touching what already works.

## Features

- Instagram: liked posts, saved collections, DM-shared content
- Twitter/X: liked tweets, recursively expanded to full threads, parent chains, quoted tweets, and liked replies
- Uploads to per-category Telegram channels, with error notifications
- SQLite tracking (shared schema across platforms) to avoid re-processing
- Exponential backoff on rate limits
- **Optional**: VLM-generated media descriptions + local embeddings for hybrid semantic search (Milvus Lite)

## Setup

### Prerequisites

- Python 3.13+, [uv](https://docs.astral.sh/uv/)
- A Telegram bot token ([create one](https://core.telegram.org/bots#6-botfather))
- Instagram account credentials, and/or Twitter `auth_token`/`ct0` cookies

### Installation

```bash
cd social-archiver
uv sync
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` — shared settings (Telegram bot, embedding/VLM) live at the top, platform-specific credentials and channel IDs are grouped below.

## Usage

Each platform is its own module, run independently:

```bash
# Instagram
uv run python -m social_archiver.platforms.instagram --once      # fetch + exit
uv run python -m social_archiver.platforms.instagram --history   # full history
uv run python -m social_archiver.platforms.instagram --init      # history, then daemon
uv run python -m social_archiver.platforms.instagram             # daemon mode
uv run python -m social_archiver.platforms.instagram --once --category saved

# Twitter/X
uv run python -m social_archiver.platforms.twitter --once
uv run python -m social_archiver.platforms.twitter --history
uv run python -m social_archiver.platforms.twitter --init
uv run python -m social_archiver.platforms.twitter
```

### Search the archive

```bash
uv run python scripts/search.py "your query" --platform instagram --category likes
uv run python scripts/search.py "your query" --platform twitter --category likes
```

### Instagram maintenance scripts

```bash
uv run python scripts/instagram_retry_failed_embeddings.py --dry-run
uv run python scripts/instagram_retry_large_files.py --dry-run
```

## How It Works

Both platforms follow the same shape, built on shared infrastructure in `social_archiver/core/` and `social_archiver/llm/`:

1. Fetch new content since the last run (cursor/dedup against the DB).
2. Download media, upload to the configured Telegram channel(s).
3. Optionally: VLM-describe media, embed with a local model, store in Milvus for semantic search.
4. Track everything in a per-platform SQLite database (`data/<platform>.db`).

Twitter additionally expands every liked tweet recursively — self-reply chains, parent chains, quoted tweets, and liked replies — so the archive matches what you'd see on the Twitter frontend, not just the isolated liked tweet.

## Project Structure

```
social_archiver/
├── core/                   # shared: database, milvus, telegram, downloader, scheduler
├── llm/                    # shared: VLM clients (Vertex/Gemini/OpenRouter), local embedder, reranker
└── platforms/
    ├── instagram/
    └── twitter/

scripts/
├── search.py                              # hybrid search CLI, --platform flag
├── instagram_retry_failed_embeddings.py
├── instagram_retry_large_files.py
└── delete_old_posts.py

data/                        # sqlite + milvus lite files (gitignored)
downloads/{instagram,twitter}/
logs/
```

## Logging

`logs/instagram.log` / `logs/twitter.log` (DEBUG, rotates daily, 30-day retention), plus INFO-level console output.

## Notes

- Instagram IGTV posts are skipped by default.
- Instagram session persists to `data/instagram_session.json`.
- Automating Instagram/Twitter violates their ToS; use at your own risk.
- Files exceeding `TELEGRAM_MAX_FILE_SIZE_MB` are skipped and logged — see below for self-hosted Bot API to raise the limit.

## Large Files (>50 MB)

Telegram Bot API caps uploads at 50 MB. To go higher:

1. Get API credentials from https://my.telegram.org
2. Add to `.env`: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_API_URL=http://localhost:8081`, `TELEGRAM_MAX_FILE_SIZE_MB=2000`
3. `docker compose -f docker-compose.telegram-api.yml up -d`
4. Logout the bot from the official API: `curl "https://api.telegram.org/bot<TOKEN>/logout"`
5. `uv run python scripts/instagram_retry_large_files.py`

## Troubleshooting

- **Instagram challenge required**: manual verification needed; check error notifications.
- **Rate limits**: increase `CHECK_INTERVAL_MINUTES`.
- **Instagram session expired**: delete `data/instagram_session.json` to force a fresh login.
- **Twitter 401/credential errors**: `auth_token`/`ct0` cookies expired — grab fresh ones from the browser.

## Embeddings (Optional)

See [docs/EMBEDDINGS.md](docs/EMBEDDINGS.md). Set `EMBEDDING_ENABLED=true` and configure `VLM_PROVIDER` in `.env`.
