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

```bash
# First-time setup: Fetch ALL history then start daemon
uv run python -m social_archiver.platforms.instagram --init

# One-time fetch (last 200 items per category)
uv run python -m social_archiver.platforms.instagram --once

# Fetch ALL history (entire Instagram archive)
uv run python -m social_archiver.platforms.instagram --history

# Daemon mode only (runs every 30min forever)
uv run python -m social_archiver.platforms.instagram
```

**Recommendation:** Use `--init` on first run to archive everything, then it automatically continues in daemon mode.

## How It Works

### Categories
- **Likes**: Posts you liked
- **Saved**: Posts saved to collections (organized by collection name)
- **Shared**: Posts shared with you via DM

### Fetch Behavior
- **Default**: Last 200 items per category (configurable via `FETCH_BATCH_SIZE`)
- **--history**: ALL items (paginated, may take hours)
- **Daemon**: Checks every 30min (configurable via `CHECK_INTERVAL_MINUTES`)

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

**Note:** Downloads are automatically deleted after successful Telegram upload by default (controlled via `CLEANUP_DOWNLOADS`). Main storage is in Telegram.

## Common Scenarios

### After Downtime (2-5 days)
```bash
# Fetches last 200 items by default (covers ~3-5 days)
# To fetch more items, set FETCH_BATCH_SIZE in .env
uv run python -m social_archiver.platforms.instagram --once
```

### After Long Downtime (>1 week)
```bash
# Fetch everything since last run
uv run python -m social_archiver.platforms.instagram --history --once
```

### Production Deployment
```bash
# Run as daemon with auto-restart
uv run python -m social_archiver.platforms.instagram
```

### Testing
```bash
# Check connection & credentials
uv run python -m social_archiver.platforms.instagram --once
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
ExecStart=/home/your_user/.local/bin/uv run python -m social_archiver.platforms.instagram
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
uv run python -m social_archiver.platforms.instagram

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
*/30 * * * * cd /path/to/social-archiver && /home/user/.local/bin/uv run python -m social_archiver.platforms.instagram --once >> /path/to/logs/cron.log 2>&1
```

## Monitoring

### Check Status
```bash
# View recent logs
tail -f logs/instagram.log

# Check database stats
sqlite3 data/instagram.db "SELECT category, COUNT(*) as total, SUM(CASE WHEN status='uploaded' THEN 1 ELSE 0 END) as uploaded FROM items WHERE platform='instagram' GROUP BY category"

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
uv run python -m social_archiver.platforms.instagram --once
```

### Missing .env Variables
```bash
# Validate config
uv run python -c "from social_archiver.platforms.instagram import config; config.validate_config()"
```

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
SELECT item_id, category, author_username, metadata
FROM items WHERE platform='instagram'
ORDER BY fetched_at DESC
LIMIT 10;
```

### Reset Category
```bash
sqlite3 data/instagram.db "DELETE FROM items WHERE platform='instagram' AND category='saved'"
uv run python -m social_archiver.platforms.instagram --once
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
