# Social Archiver

Archives what you've liked, saved and bookmarked across social platforms to your own disk,
optionally mirrors it to Telegram, and optionally makes it semantically searchable.

Supports **Instagram** (likes, saved collections, DM shares), **Reddit** (saved, upvoted,
downvoted, your own posts) and **Twitter/X** (likes and bookmarks, expanded to full threads).

## How it works

Every platform runs the same independent, resumable jobs. Each tracks its own status column in
a per-platform SQLite database, so any job can be run, interrupted, or skipped without
affecting the others:

| Job | What it does | Needs |
|---|---|---|
| `archive` | Fetch new content, download media to disk | platform credentials |
| `upload` | Send archived items to Telegram | bot token + channel ids |
| `embed` | Index for semantic search | an embedding server |
| `run` / `daemon` | All of the above, once / on an interval | |

Archiving never depends on Telegram or embeddings — run it alone and the rest can catch up
later. Failed items are retried with `--retry-failed`.

Long history walks resume: a stopped run continues from its stored cursor rather than paging
from the top again.

## Setup

### 1. Install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
```

### 2. Credentials

Configure only the platforms you want. `.env.example` documents every setting.

**Instagram** — username and password. The session persists to `data/instagram_session.json`,
so login happens once. To archive DM shares, set `INSTAGRAM_DM_USERNAME` to the account whose
thread should be walked; leave it empty and that category is skipped.

```env
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=
INSTAGRAM_DM_USERNAME=
```

**Reddit** — create an *installed app* at https://www.reddit.com/prefs/apps with redirect uri
`http://localhost:8080`, which issues a client id and no secret. Then run the one-time OAuth
helper; it prints a refresh token, so no password is stored and 2FA stays in the browser:

```bash
REDDIT_CLIENT_ID=your_id uv run python scripts/reddit_auth.py
```

```env
REDDIT_CLIENT_ID=
REDDIT_REFRESH_TOKEN=
```

**Twitter/X** — cookie auth. On `x.com`, DevTools → Application → Cookies, copy `auth_token`
and `ct0`. They expire periodically; a 401 means fetch fresh ones.

```env
TWITTER_AUTH_TOKEN=
TWITTER_CT0=
```

### 3. Telegram (optional)

Only needed for `upload`. Create a bot with [@BotFather](https://core.telegram.org/bots#6-botfather),
then set the per-category channel ids (`TELEGRAM_CHAT_LIKES`, `REDDIT_CHAT_SAVED`, ...). Items
archived without a channel simply wait; configure one later and `upload` picks them up.

### 4. Embeddings and search (optional)

Set `EMBEDDING_ENABLED=true` and point `EMBED_URL` at any OpenAI-compatible embedding server —
vLLM, llama.cpp or TEI. The archiver carries no ML runtime of its own, so the model runs
wherever it has the hardware for it:

```bash
vllm serve jinaai/jina-embeddings-v5-omni-small --runner pooling \
  --trust-remote-code --hf-overrides '{"task": "retrieval"}'
```

```env
EMBEDDING_ENABLED=true
EMBED_URL=http://localhost:8000/v1/embeddings
EMBED_MODEL=jinaai/jina-embeddings-v5-omni-small
```

`embed` captions media with a VLM (`VLM_PROVIDER`: `vertex`, `gemini` or `openrouter`), joins
that with the post's own text, and stores the vector in Milvus Lite beside the archive.

Reranking is optional and needs a second server, since a reranker is a different model from an
embedder. Leave `RERANK_URL` empty and search returns plain vector order.

## The service

`python -m social_archiver.web` is the whole thing: a web UI on port 8787, a scheduler, and
one worker per platform.

The scheduler and the UI both *enqueue* jobs; only the worker runs them. So a timer firing
during a long walk queues behind it instead of racing it, and the same platform can never
archive twice at once. Different platforms run in parallel — separate APIs, separate limits.

The queue lives in `data/jobs.db`, so a restart loses nothing and a run that died is marked
interrupted rather than vanishing. The UI shows the queue, recent runs and their errors.

It has no authentication and can start jobs, so keep it on the LAN.

```bash
uv run python -m social_archiver.web    # WEB_HOST / WEB_PORT to change where it binds
```

## Usage

```bash
# One platform, one job
uv run python -m social_archiver.platforms.reddit archive
uv run python -m social_archiver.platforms.instagram archive --category saved
uv run python -m social_archiver.platforms.twitter upload --retry-failed

# Full history rather than only what is new
uv run python -m social_archiver.platforms.reddit archive --history

# Everything in order, once / forever
uv run python -m social_archiver.platforms.twitter run
uv run python -m social_archiver.platforms.twitter daemon
```

Search the archive:

```bash
uv run python scripts/search.py "your query" --platform reddit --category saved
```

### Account exports

Reddit and Twitter cap how far their APIs page back. Request your account export from the
platform, point the archiver at the zip, and it backfills past that ceiling in resumable
chunks:

```env
REDDIT_EXPORT_PATH=./data/reddit_export.zip
TWITTER_EXPORT_PATH=./data/twitter_export.zip
```

## Docker

Images publish to `ghcr.io/aunali321/social-archiver`. `ARCHIVE_ROOT` is the host directory
holding `data/`, `downloads/` and `logs/`; leave it unset to use the compose file's own
directory.

```bash
docker compose up -d                                                # all three daemons
docker compose --profile history run --rm reddit-archiver-history   # one-time full backfill
```

`downloads/` is the archive itself and is mounted as a volume — with `CLEANUP_DOWNLOADS=false`
nothing is ever deleted, so it must not live in the container's writable layer.

## Layout

```
social_archiver/
├── core/         database, jobs, milvus, telegram, downloader, scheduler, cli
├── llm/          VLM clients (vertex/gemini/openrouter), embed + rerank HTTP clients
└── platforms/    instagram/ reddit/ twitter/ — archiver, embedder, port, fetchers, __main__

data/             sqlite + milvus lite + session + exports   (gitignored)
downloads/        the archived media                          (gitignored)
logs/             per-platform, rotated daily                 (gitignored)
```

## Large files

Telegram's Bot API caps uploads at 50 MB. To go higher, run a self-hosted Bot API server:

1. Get API credentials from https://my.telegram.org
2. Set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_API_URL=http://localhost:8081`,
   `TELEGRAM_MAX_FILE_SIZE_MB=2000`
3. `docker compose -f docker-compose.telegram-api.yml up -d`
4. Log the bot out of the official API: `curl "https://api.telegram.org/bot<TOKEN>/logout"`
5. `uv run python -m social_archiver.platforms.instagram upload --retry-failed`

## Troubleshooting

- **Instagram `FeedbackRequired`** — a soft block on that endpoint, caused by repeated
  requests. Retrying makes it worse; leave that category alone and it clears on its own.
- **Instagram challenge required** — manual verification; check the error notification.
- **Instagram session expired** — delete `data/instagram_session.json` to force a fresh login.
- **Twitter 401** — `auth_token`/`ct0` expired, fetch fresh cookies.
- **Rate limits generally** — raise `CHECK_INTERVAL_MINUTES`.

## Notes

- Instagram IGTV posts are skipped.
- Reddit link posts to YouTube keep their url, but the video itself is not downloaded.
- Automating these platforms is against their terms of service; use at your own risk.
