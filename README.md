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

Each platform is its own module with three independent, resumable jobs. All
state lives in SQLite, so any job can be run (or interrupted) at any time and
picks up where it left off:

```bash
# Archive: fetch new content and download media to disk. No Telegram, no VLM.
uv run python -m social_archiver.platforms.twitter archive
uv run python -m social_archiver.platforms.twitter archive --history
uv run python -m social_archiver.platforms.instagram archive --category saved

# Upload: send everything archived-but-not-yet-uploaded to Telegram.
uv run python -m social_archiver.platforms.twitter upload
uv run python -m social_archiver.platforms.twitter upload --retry-failed

# Embed: VLM descriptions + search embeddings for the archived backlog.
uv run python -m social_archiver.platforms.twitter embed
uv run python -m social_archiver.platforms.twitter embed --retry-failed

# All three in order, once / on an interval:
uv run python -m social_archiver.platforms.twitter run
uv run python -m social_archiver.platforms.twitter daemon
```

Instagram exposes the same commands. Downloaded media stays on disk until both
upload and embedding (when enabled) are done with it; if a file is ever missing
later, it is re-downloaded from the URLs stored at archive time.

### Search the archive

```bash
uv run python scripts/search.py "your query" --platform instagram --category likes
uv run python scripts/search.py "your query" --platform twitter --category likes
```

## How It Works

Both platforms follow the same shape, built on shared infrastructure in `social_archiver/core/` and `social_archiver/llm/`. Each item moves through three independent stages, each tracked by its own status column in a per-platform SQLite database (`data/<platform>.db`):

1. **archive** — fetch new content since the last run (cursor/dedup against the DB), record it, download media.
2. **upload** — send archived items to the configured Telegram channel(s).
3. **embed** (optional) — VLM-describe media, embed with a local model, store in Milvus for semantic search.

A stage failing or being skipped never blocks the others; failed items are retried with `--retry-failed` (large files, for example, after configuring a self-hosted Bot API server).

Twitter additionally expands every liked tweet recursively — self-reply chains, parent chains, quoted tweets, and liked replies — so the archive matches what you'd see on the Twitter frontend, not just the isolated liked tweet.

## Project Structure

```
social_archiver/
├── core/                   # shared: database, jobs (upload/cleanup), milvus, telegram, downloader, scheduler, cli
├── llm/                    # shared: VLM clients (Vertex/Gemini/OpenRouter), local embedder, reranker
└── platforms/
    ├── instagram/          # archiver, embedder, port (captions/folders), fetchers, __main__
    └── twitter/            # archiver, embedder, port, expander, client, __main__

scripts/
├── search.py               # hybrid search CLI, --platform flag
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
5. `uv run python -m social_archiver.platforms.instagram upload --retry-failed`

## Troubleshooting

- **Instagram challenge required**: manual verification needed; check error notifications.
- **Rate limits**: increase `CHECK_INTERVAL_MINUTES`.
- **Instagram session expired**: delete `data/instagram_session.json` to force a fresh login.
- **Twitter 401/credential errors**: `auth_token`/`ct0` cookies expired — grab fresh ones from the browser.

## Embeddings (Optional)

See [docs/EMBEDDINGS.md](docs/EMBEDDINGS.md). Set `EMBEDDING_ENABLED=true` and configure `VLM_PROVIDER` in `.env`.
