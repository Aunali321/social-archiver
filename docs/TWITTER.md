# Twitter/X Archiver

Archives your Twitter/X liked and bookmarked tweets to Telegram with full thread, quote, and reply context. Uses Twitter's internal GraphQL API via cookie-based auth. Likes and bookmarks each run when their Telegram chat is configured.

## Setup

1. Get your Twitter cookies from browser DevTools:
   - Go to `x.com`, open DevTools → Application → Cookies
   - Copy `auth_token` and `ct0` values

2. Set environment variables in `.env`:
   ```
   TWITTER_AUTH_TOKEN=your_auth_token_cookie
   TWITTER_CT0=your_ct0_cookie
   TELEGRAM_BOT_TOKEN=your_bot_token
   TWITTER_CHAT_LIKES=-your_chat_id
   ```

3. Run:
   ```bash
   # First time — fetch all historical likes + expand
   uv run python -m social_archiver.platforms.twitter --init

   # One-shot (recent likes only)
   uv run python -m social_archiver.platforms.twitter --once

   # Full history without daemon
   uv run python -m social_archiver.platforms.twitter --history

   # Daemon mode (checks every CHECK_INTERVAL_MINUTES)
   uv run python -m social_archiver.platforms.twitter --daemon
   ```

## What it does

When you like a tweet, the archiver doesn't just save that tweet. It recursively expands everything connected to it:

| You liked...                        | What gets saved                                                        |
|-------------------------------------|------------------------------------------------------------------------|
| A standalone tweet                  | The tweet itself                                                       |
| A tweet in a thread (e.g. 3/7)     | The entire self-reply chain (all 7 tweets)                             |
| A quote tweet                       | The quote + the quoted tweet + the quoted tweet's thread               |
| A reply                             | The reply + every parent up to the conversation root + their threads   |
| A retweet                           | The original tweet + its thread                                        |
| A quote of a quote                  | Recursive: all quotes and their threads, all the way down              |
| A tweet linking to another tweet    | The linked tweet + its thread (URL in text, not a quote card)          |

Additionally, for every tweet in the expanded set, if you also liked any of its replies, those are pulled in and expanded too.

Threads are paginated until the cursor is exhausted, so long threads are captured in full.

Every tweet is tagged with an `origin` explaining why it was saved:
- `liked` / `bookmarked` — you saved it directly
- `thread` — part of a self-reply chain connected to a seed tweet
- `parent` — ancestor in a reply chain (context for a liked reply)
- `quoted` — was quoted by a tweet in the set
- `linked` — referenced by URL in a tweet's text
- `retweet` — original tweet behind a retweet you liked

If a tweet later gets liked/bookmarked directly, its origin is upgraded from the context value to the seed value.

### Tombstones

Tweets that exist in the reply graph but are unavailable (deleted, protected account, suspended author) are recorded as tombstone rows: `status = 'tombstone'`, with the reason in `text` and the URL as `x.com/i/status/<id>`. They are not uploaded to Telegram or embedded, but the DB records that context existed and was already gone at archive time.

## API call estimate

The archiver minimizes calls by caching conversations:

| Phase | Calls | Notes |
|-------|-------|-------|
| Auth verification | 1 | `verify_credentials` |
| Fetch likes | `ceil(total_likes / 20)` | Paginated, 20 per page |
| Expand conversations | 1 per unique `conversation_id` | Cached — liking 5 tweets in the same thread = 1 call, not 5 |
| Expand quoted tweets | 1 per quoted tweet in a different conversation | Skipped if conversation already cached |

**Examples:**
- 100 likes, all standalone tweets, no quotes → `5 + 100 = ~105 calls`
- 100 likes, 60 unique conversations, 10 quotes in new conversations → `5 + 60 + 10 = ~75 calls`
- 100 likes, 20 unique conversations (lots of thread/reply activity) → `5 + 20 = ~25 calls`
- 1000 likes, 500 unique conversations, 50 quotes → `50 + 500 + 50 = ~600 calls`

Subsequent runs (daemon/--once) only fetch new likes since the last run and skip tweets already in the DB, so ongoing costs are minimal.

Thread pagination is uncapped: a conversation with heavy reply activity (viral tweet) can take many TweetDetail pages. Each page beyond the first costs one call plus `page_delay`.

## Database schema

Twitter rows live in the same shared `items` table as every other platform (`social_archiver/core/database.py`), using its relationship-graph columns:

- `conversation_id` — groups tweets in the same thread
- `in_reply_to_status_id` — parent tweet (reply chain)
- `quoted_tweet_id` — what this tweet quotes
- `retweeted_tweet_id` — original tweet if this is a retweet
- `origin` — why this tweet was saved
- `discovered_via_item_id` — which liked tweet caused this to be discovered
- `thread_position` — `root`, `middle`, `end`, or `standalone`

This is designed so a frontend can reconstruct the full Twitter view — threads, quotes, reply chains — using standard SQL joins.

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TWITTER_AUTH_TOKEN` | Yes | — | `auth_token` cookie from x.com |
| `TWITTER_CT0` | Yes | — | `ct0` cookie from x.com |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token |
| `TWITTER_CHAT_LIKES` | At least one of these two | — | Telegram chat ID for likes |
| `TWITTER_CHAT_BOOKMARKS` | At least one of these two | — | Telegram chat ID for bookmarks |
| `TELEGRAM_CHAT_ERRORS` | No | — | Chat ID for error notifications |
| `CHECK_INTERVAL_MINUTES` | No | 30 | Daemon check interval |
| `FETCH_BATCH_SIZE` | No | 200 | Max likes to fetch per run (0 = all) |
| `CLEANUP_DOWNLOADS` | No | true | Delete media after upload |
| `EMBEDDING_ENABLED` | No | false | Enable vector embeddings |
| `TELEGRAM_MAX_FILE_SIZE_MB` | No | 50 | Max file size for Telegram uploads |
| `TELEGRAM_BOT_API_URL` | No | — | Self-hosted Telegram Bot API URL |
