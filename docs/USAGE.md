# Instagram Archiver - Usage Guide

## Quick Start

### 1. Setup
```bash
# Copy environment template
cp .env.example .env

# Edit with your credentials
nano .env
```

**Required:**
- `INSTAGRAM_USERNAME` - Your Instagram username
- `INSTAGRAM_PASSWORD` - Your Instagram password
- `INSTAGRAM_DM_USERNAME` - Username to fetch DM shares from
- `TELEGRAM_BOT_TOKEN` - Get from @BotFather
- `TELEGRAM_CHAT_*` - Chat IDs for likes/saved/shared/errors

### 2. Run

Three independent, resumable jobs; run them together or separately:

```bash
# Everything once: archive -> upload -> embed
uv run python -m social_archiver.platforms.instagram run

# First-time setup: full history, then keep the daemon running
uv run python -m social_archiver.platforms.instagram run --history
uv run python -m social_archiver.platforms.instagram daemon

# Individual jobs
uv run python -m social_archiver.platforms.instagram archive              # fetch + download only
uv run python -m social_archiver.platforms.instagram archive --history    # entire Instagram archive
uv run python -m social_archiver.platforms.instagram archive --category saved
uv run python -m social_archiver.platforms.instagram upload               # Telegram later, when you have time
uv run python -m social_archiver.platforms.instagram embed                # embeddings later, when you have time
```

Any job can be interrupted and re-run; it resumes from the database. `upload`
and `embed` accept `--retry-failed` to also retry previously failed items.

## How It Works

### Categories
- **Likes**: Posts you liked
- **Saved**: Posts saved to collections (organized by collection name)
- **Shared**: Posts shared with you via DM

A category is enabled by configuring its Telegram channel ID.

### Fetch Behavior
- **Default**: Last 200 items per category (configurable via `FETCH_BATCH_SIZE`)
- **--history**: ALL items (paginated, may take hours)
- **Daemon**: Runs all jobs every 30min (configurable via `CHECK_INTERVAL_MINUTES`)

### Smart Deduplication
- Database tracks all processed items by `item_id`
- Skips already-downloaded content
- Safe to run multiple times

### Telegram Output

**Likes:**
```
[Caption]

👤 @author
🔗 https://instagram.com/p/ABC123
📅 2024-01-15 10:30:00
```

**Saved:**
```
[Caption]

📁 Memes
👤 @author
🔗 https://instagram.com/p/XYZ789
📅 2024-01-15 12:45:00
```

**Shared:**
```
[Caption]

👤 @author
📤 Shared by @friend
🔗 https://instagram.com/p/DEF456
📅 2024-01-15 15:00:00
```

### File Organization
```
downloads/instagram/    # Temporary (deleted after upload by default)
├── likes/
│   └── {item_id}.jpg
├── saved/
│   ├── Memes/          # Organized by collection
│   │   └── {item_id}.mp4
│   └── Recipes/
│       └── {item_id}.jpg
└── shared/
    └── {item_id}.mp4
```

**Note:** Downloads are deleted once every enabled consumer is done with them — after upload, and after embedding when `EMBEDDING_ENABLED=true` (controlled via `CLEANUP_DOWNLOADS`). Main storage is in Telegram. If a file is needed again later, it is re-downloaded from the URLs stored at archive time.

## Common Scenarios

### After Downtime (2-5 days)
```bash
# Fetches last 200 items by default (covers ~3-5 days)
# To fetch more items, set FETCH_BATCH_SIZE in .env
uv run python -m social_archiver.platforms.instagram run
```

### After Long Downtime (>1 week)
```bash
# Fetch everything since last run
uv run python -m social_archiver.platforms.instagram run --history
```

### No Time For Uploads/Embeddings Right Now
```bash
# Secure the content first; the rest can happen any time later
uv run python -m social_archiver.platforms.instagram archive
# ...later:
uv run python -m social_archiver.platforms.instagram upload
uv run python -m social_archiver.platforms.instagram embed
```

### Production Deployment
```bash
# Run as daemon with auto-restart
uv run python -m social_archiver.platforms.instagram daemon
```

### Testing
```bash
# Check connection & credentials
uv run python -m social_archiver.platforms.instagram archive
# Watch logs/instagram.log
```

## Deployment Options

### Option 1: systemd (Linux)

Create `/etc/systemd/system/instagram-archiver.service`:
```ini
[Unit]
Description=Instagram to Telegram Archiver
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/social-archiver
Environment="PATH=/path/to/.local/bin:/usr/bin"
ExecStart=/home/your_user/.local/bin/uv run python -m social_archiver.platforms.instagram daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable instagram-archiver
sudo systemctl start instagram-archiver
sudo systemctl status instagram-archiver
```

View logs:
```bash
sudo journalctl -u instagram-archiver -f
```

### Option 2: Docker Compose (Recommended)

```bash
# Create .env file first
cp .env.example .env
nano .env

# First-time setup: Fetch ALL history then start daemon
docker compose up instagram-archiver-history --profile history
# Wait for completion, then start daemon
docker compose up -d instagram-archiver

# Or just start daemon (skips history)
docker compose up -d instagram-archiver

# View logs
docker compose logs -f instagram-archiver

# Stop
docker compose down
```

The docker-compose.yml includes:
- Main service (daemon mode)
- History service (one-time full fetch)
- Persistent volumes for logs, database, session
- Downloads NOT persisted (deleted after upload)

### Option 3: Screen/tmux (Simple)

```bash
# Start screen session
screen -S archiver

# Run archiver
cd /path/to/social-archiver
uv run python -m social_archiver.platforms.instagram daemon

# Detach: Ctrl+A, D
# Reattach: screen -r archiver
```

### Option 4: Cron (Scheduled runs)

Edit crontab:
```bash
crontab -e
```

Add line (runs every 30min):
```cron
*/30 * * * * cd /path/to/social-archiver && /home/user/.local/bin/uv run python -m social_archiver.platforms.instagram run >> /path/to/logs/cron.log 2>&1
```

## Monitoring

### Check Status
```bash
# View recent logs
tail -f logs/instagram.log

# Check database stats
uv run python scripts/search.py --platform instagram stats

# Check disk usage
du -sh downloads/instagram/
```

### Health Checks
- Error notifications sent to `TELEGRAM_CHAT_ERRORS`
- Log rotation: 30 days retention
- Session persists in `data/instagram_session.json`

## Troubleshooting

### Instagram Login Failed
```bash
# Delete session and retry
rm data/instagram_session.json
uv run python -m social_archiver.platforms.instagram archive
```

### Missing .env Variables
Every command validates the variables it needs on startup and names the missing ones.

### Telegram Upload Timeout
- Check internet connection
- Verify bot token
- Ensure bot is admin in target chats

### Rate Limiting
- Delays: 2-5s between Instagram requests
- Exponential backoff on errors
- Session reuse prevents repeated logins

## Database Management

### View Processed Items
```bash
sqlite3 data/instagram.db
SELECT item_id, category, author_username, archive_status, upload_status, embed_status
FROM items WHERE platform='instagram'
ORDER BY fetched_at DESC
LIMIT 10;
```

### Reset Category
```bash
sqlite3 data/instagram.db "DELETE FROM items WHERE platform='instagram' AND category='saved'"
uv run python -m social_archiver.platforms.instagram run
```

### Backup Database
```bash
cp data/instagram.db data/instagram.backup.db
```

## Advanced

### Custom Fetch Interval
```bash
# Edit .env
CHECK_INTERVAL_MINUTES=60  # Check every hour
```

### Custom Batch Size
```bash
# Edit .env
FETCH_BATCH_SIZE=500  # Fetch last 500 items per category (default: 200)
# Useful if you have high activity or frequent downtime
```

### Keep Downloaded Files
```bash
# Edit .env
CLEANUP_DOWNLOADS=false  # Keep files after upload (default: true)
# Set to false if you want local backups in addition to Telegram
```

### Collection-Specific Processing
Currently processes all collections. To filter:
1. Modify `fetchers/saved.py`
2. Add collection whitelist/blacklist

### Proxy Support
Add to Instagram client init (requires code modification).
