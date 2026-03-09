# Instagram to Telegram Archiver

Automated bot that downloads Instagram content (likes, saved posts, DM shares) and uploads to Telegram groups.

## Features

- Downloads liked posts, saved collections, and DM-shared content
- Uploads to separate Telegram groups per category
- Persistent session management (avoids repeated logins)
- SQLite database for tracking processed content
- Exponential backoff for rate limiting
- Error notifications via Telegram
- Supports photos, videos, carousels, reels, and stories
- **Optional**: Generate embeddings for semantic search (using Qwen3-VL-Embedding)

## Setup

### Prerequisites

- Python 3.13+
- Instagram account credentials
- Telegram bot token ([create one](https://core.telegram.org/bots#6-botfather))

### Installation

```bash
# Clone or download this repository
cd insta-archiver

# Install dependencies using uv
uv sync
```

### Configuration

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Edit `.env` with your credentials:
```env
INSTAGRAM_USERNAME=your_username
INSTAGRAM_PASSWORD=your_password
INSTAGRAM_DM_USERNAME=your_private_account_username

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_LIKES=-5254830835
TELEGRAM_CHAT_SAVED=-5214899548
TELEGRAM_CHAT_SHARED=-4942383347
TELEGRAM_CHAT_ERRORS=851091169

CHECK_INTERVAL_MINUTES=30

# File size limits (optional)
TELEGRAM_MAX_FILE_SIZE_MB=50  # Default: 50 MB (Bot API limit)
TELEGRAM_BOT_API_URL=         # For self-hosted API (e.g., http://localhost:8081)
```

## Usage

### Run in daemon mode (default)
```bash
uv run python -m insta_archiver
```

### Run once and exit
```bash
uv run python -m insta_archiver --once
```

### Download all history
```bash
uv run python -m insta_archiver --history
```

### Run once with history (then exit)
```bash
uv run python -m insta_archiver --once --history
```

### Process specific category only
```bash
# Only saved posts
uv run python -m insta_archiver --once --category saved

# Only likes
uv run python -m insta_archiver --once --category likes --history
```

### Retry failed large files
If files failed due to size limits, retry after setting up self-hosted Bot API:
```bash
# Dry run to see what would be retried
python retry_large_files.py --dry-run

# Retry all failed large files
python retry_large_files.py

# Retry only from 'saved' category
python retry_large_files.py --category saved
```

## How It Works

1. Logs into Instagram using stored session (or fresh login)
2. Every 30 minutes (configurable):
   - Fetches new liked posts → uploads to `TELEGRAM_CHAT_LIKES`
   - Fetches saved collections → uploads to `TELEGRAM_CHAT_SAVED`
   - Fetches DM shares → uploads to `TELEGRAM_CHAT_SHARED`
3. Tracks processed content in SQLite database to avoid duplicates
4. Downloads media locally (organized by category in `downloads/`)
5. Uploads to Telegram with formatted captions (author, URL, timestamp)
6. Sends error notifications to `TELEGRAM_CHAT_ERRORS`

## Project Structure

```
insta_archiver/
├── __main__.py          # CLI entry point
├── config.py            # Configuration loading
├── database.py          # SQLite operations
├── instagram_client.py  # Instagram session management
├── telegram_client.py   # Telegram upload logic
├── downloader.py        # Media download with retry
├── processor.py         # Core processing logic
├── scheduler.py         # Periodic execution
├── utils.py             # Logging setup
└── fetchers/
    ├── likes.py         # Fetch liked media
    ├── saved.py         # Fetch saved collections
    └── shared.py        # Fetch DM shares
```

## Logging

Logs are written to:
- `logs/insta_archiver.log` (DEBUG level, rotates daily, keeps 30 days)
- Console output (INFO level)

## Notes

- IGTV posts are skipped by default
- Session persists to `session.json` to avoid Instagram rate limits
- Downloads stored in `downloads/likes/`, `downloads/saved/`, `downloads/shared/`
- Database file: `database.db`
- Instagram automation violates ToS; use at your own risk
- Files exceeding `TELEGRAM_MAX_FILE_SIZE_MB` are skipped and logged
- Use self-hosted Bot API for files >50 MB (see below)

## Large Files (>50 MB)

Telegram Bot API has a 50 MB upload limit. For larger files:

1. Get API credentials from https://my.telegram.org
2. Add to `.env`:
   ```env
   TELEGRAM_API_ID=your_api_id
   TELEGRAM_API_HASH=your_api_hash
   TELEGRAM_BOT_API_URL=http://localhost:8081
   TELEGRAM_MAX_FILE_SIZE_MB=2000
   ```
3. Start self-hosted Bot API server:
   ```bash
   docker compose -f docker-compose.telegram-api.yml up -d
   ```
4. Logout bot from official API:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/logout"
   ```
5. Retry failed files:
   ```bash
   python retry_large_files.py
   ```

## Troubleshooting

- **Challenge required**: Instagram may require manual verification; check error notifications
- **Rate limits**: Increase `CHECK_INTERVAL_MINUTES` or reduce content volume
- **Session expired**: Delete `session.json` to force fresh login

## Embeddings (Optional)

This archiver supports generating multimodal embeddings for semantic search. See [EMBEDDINGS.md](EMBEDDINGS.md) for details.

Quick setup:
1. Run vLLM with Qwen3-VL-Embedding-2B on localhost:3000
2. Set `EMBEDDING_ENABLED=true` in .env
3. Search your archive: `python search_embeddings.py search "your query"`
