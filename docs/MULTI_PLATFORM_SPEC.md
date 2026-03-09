# Multi-Platform Social Media Archiver - Technical Specification

## Overview

Extend the existing Instagram archiver to support Twitter/X and future platforms (Reddit). Rename package from `insta_archiver` to `social_archiver`.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Package name | `social_archiver` | Multi-platform support |
| Code structure | `platforms/` folder | Isolate platform-specific code |
| Database | Composite key (platform + item_pk) | Clean cross-platform deduplication |
| Twitter integration | Python port of bird library GraphQL API | Native integration, no Node.js dependency |
| Twitter auth | Manual cookie entry (AUTH_TOKEN, CT0) | Simple, user-controlled |
| Twitter destination | Single Telegram channel | User preference |
| Rate limiting | Conservative (5-10s delays) | X blocks automated requests aggressively |
| Processing | Parallel (Instagram + Twitter) | User preference |

## MVP Scope (Twitter)

1. Likes - tweets user has liked
2. Bookmarks - tweets user has bookmarked

User tweets archiving is not priority for MVP.

---

## Database Schema Changes

### Current Schema
```sql
-- media_pk is Instagram-specific INTEGER
media_pk INTEGER PRIMARY KEY
```

### New Schema
```sql
CREATE TABLE processed_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,           -- 'instagram', 'twitter', 'reddit'
    item_pk TEXT NOT NULL,            -- platform-specific ID (string for flexibility)
    item_id TEXT,                     -- secondary ID if platform uses one
    item_code TEXT,                   -- shortcode/slug for URLs
    category TEXT NOT NULL,           -- 'likes', 'bookmarks', 'saved', 'shared'
    media_type TEXT,                  -- 'photo', 'video', 'album', 'text'
    product_type TEXT,                -- platform-specific subtype
    author_username TEXT NOT NULL,
    author_user_id TEXT,
    caption TEXT,
    post_url TEXT NOT NULL,
    taken_at TIMESTAMP,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    downloaded_at TIMESTAMP,
    uploaded_at TIMESTAMP,
    embedded_at TIMESTAMP,
    status TEXT NOT NULL,             -- 'pending', 'downloaded', 'uploaded', 'failed', 'skipped'
    embedding_status TEXT,
    error_message TEXT,
    local_paths TEXT,                 -- JSON array
    telegram_message_ids TEXT,        -- JSON array
    metadata TEXT,                    -- JSON object for platform-specific data
    UNIQUE(platform, item_pk)
);

CREATE INDEX idx_platform_category ON processed_media(platform, category);
CREATE INDEX idx_status ON processed_media(status);
```

### Migration Strategy
1. Create new table with new schema
2. Migrate existing data: set `platform='instagram'`, convert `media_pk` to string
3. Drop old table, rename new table

---

## Directory Structure

```
social_archiver/
├── __init__.py
├── __main__.py                    # CLI entry point
├── config.py                      # Environment config, validation
├── database.py                    # Platform-agnostic DB operations
├── downloader.py                  # HTTP download with retry
├── utils.py                       # Logging, helpers
├── scheduler.py                   # Scheduling logic
├── processor.py                   # Main orchestration
│
├── telegram/
│   ├── __init__.py
│   └── client.py                  # Telegram upload, formatting
│
├── models/
│   ├── __init__.py
│   └── media.py                   # PlatformMedia dataclass
│
├── platforms/
│   ├── __init__.py
│   ├── base.py                    # Abstract base classes
│   │
│   ├── instagram/
│   │   ├── __init__.py
│   │   ├── client.py              # Instagram API wrapper
│   │   ├── simple_media.py        # Forgiving media parser
│   │   └── fetchers/
│   │       ├── __init__.py
│   │       ├── likes.py
│   │       ├── saved.py
│   │       └── shared.py
│   │
│   └── twitter/
│       ├── __init__.py
│       ├── client.py              # Twitter GraphQL client
│       ├── constants.py           # API URLs, query IDs
│       ├── features.py            # GraphQL feature flags
│       ├── auth.py                # Cookie handling
│       └── fetchers/
│           ├── __init__.py
│           ├── likes.py
│           └── bookmarks.py
│
└── embeddings/                    # Optional vector search
    ├── __init__.py
    ├── client.py
    ├── processor.py
    └── milvus_manager.py

downloads/
├── instagram/
│   ├── likes/
│   ├── saved/{collection}/
│   └── shared/
└── twitter/
    ├── likes/
    └── bookmarks/
```

---

## Configuration

### New Environment Variables

```bash
# Twitter/X
TWITTER_AUTH_TOKEN=             # auth_token cookie from browser
TWITTER_CT0=                    # ct0 cookie from browser
TWITTER_CHAT_ID=                # Telegram chat for all Twitter content

# Platform toggles (optional, both enabled by default)
INSTAGRAM_ENABLED=true
TWITTER_ENABLED=true

# Twitter behavior
TWITTER_CHECK_INTERVAL_MINUTES=60
TWITTER_FETCH_BATCH_SIZE=20
TWITTER_REQUEST_DELAY_MIN=5
TWITTER_REQUEST_DELAY_MAX=10
```

### Existing Variables (unchanged)
```bash
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=
INSTAGRAM_SESSIONID=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_LIKES=
TELEGRAM_CHAT_SAVED=
TELEGRAM_CHAT_SHARED=
TELEGRAM_CHAT_ERRORS=
CHECK_INTERVAL_MINUTES=30
```

---

## Platform Abstraction

### Base Classes

```python
# platforms/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from datetime import datetime

@dataclass
class PlatformMedia:
    """Normalized media item across all platforms."""
    platform: str               # 'instagram', 'twitter'
    item_pk: str                # unique ID on platform
    item_id: Optional[str]      # secondary ID
    item_code: Optional[str]    # URL slug
    category: str               # 'likes', 'bookmarks', etc.
    media_type: str             # 'photo', 'video', 'album', 'text'
    product_type: Optional[str]
    author_username: str
    author_user_id: Optional[str]
    caption: Optional[str]
    post_url: str
    taken_at: Optional[datetime]
    resources: list             # download URLs for media
    metadata: dict              # platform-specific extras

class BasePlatformClient(ABC):
    """Base class for platform API clients."""
    
    @abstractmethod
    async def login(self) -> bool:
        """Authenticate with platform."""
        pass
    
    @abstractmethod
    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        pass

class BaseFetcher(ABC):
    """Base class for content fetchers."""
    
    platform: str
    category: str
    
    @abstractmethod
    async def fetch(self, limit: Optional[int] = None) -> AsyncIterator[PlatformMedia]:
        """Fetch items from this category."""
        pass
```

---

## Twitter Client Implementation

### Authentication

```python
# platforms/twitter/auth.py

@dataclass
class TwitterCookies:
    auth_token: str
    ct0: str
    
    @property
    def cookie_header(self) -> str:
        return f"auth_token={self.auth_token}; ct0={self.ct0}"

def load_twitter_credentials() -> Optional[TwitterCookies]:
    """Load Twitter cookies from environment."""
    auth_token = os.getenv("TWITTER_AUTH_TOKEN")
    ct0 = os.getenv("TWITTER_CT0")
    if not auth_token or not ct0:
        return None
    return TwitterCookies(auth_token=auth_token, ct0=ct0)
```

### GraphQL Client

Port key methods from bird library:

```python
# platforms/twitter/client.py

class TwitterClient(BasePlatformClient):
    """Twitter GraphQL API client (Python port of bird library)."""
    
    BASE_URL = "https://x.com/i/api/graphql"
    BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
    
    def __init__(self, cookies: TwitterCookies, timeout: int = 30):
        self.cookies = cookies
        self.timeout = timeout
        self._current_user: Optional[dict] = None
        self._http_client: Optional[httpx.AsyncClient] = None
    
    def _get_headers(self) -> dict:
        return {
            "accept": "*/*",
            "authorization": f"Bearer {self.BEARER_TOKEN}",
            "x-csrf-token": self.cookies.ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "cookie": self.cookies.cookie_header,
            "user-agent": "Mozilla/5.0 ...",
            "content-type": "application/json",
        }
    
    async def get_current_user(self) -> dict:
        """Get authenticated user info."""
        pass
    
    async def get_likes(self, count: int = 20, cursor: Optional[str] = None) -> dict:
        """Fetch user's liked tweets."""
        pass
    
    async def get_bookmarks(self, count: int = 20, cursor: Optional[str] = None) -> dict:
        """Fetch user's bookmarks."""
        pass
```

### Query IDs

Twitter rotates GraphQL query IDs. Strategy:
1. Ship with known working IDs
2. If 404, attempt to refresh from Twitter's JS bundles
3. Fall back to hardcoded IDs

```python
# platforms/twitter/constants.py

QUERY_IDS = {
    "Likes": "JR2gceKucIKcVNB_9JkhsA",
    "Bookmarks": "RV1g3b8n_SGOHwkqKYSCFw",
    "Viewer": "p2CscVMruxfdq0fOHZghvQ",
}

FEATURES = {
    # Feature flags required by GraphQL endpoints
    # Copied from bird library
}
```

---

## Fetcher Implementations

### Twitter Likes Fetcher

```python
# platforms/twitter/fetchers/likes.py

class TwitterLikesFetcher(BaseFetcher):
    platform = "twitter"
    category = "likes"
    
    def __init__(self, client: TwitterClient):
        self.client = client
    
    async def fetch(self, limit: Optional[int] = None) -> AsyncIterator[PlatformMedia]:
        cursor = None
        fetched = 0
        
        while True:
            result = await self.client.get_likes(count=20, cursor=cursor)
            tweets = result.get("tweets", [])
            
            for tweet in tweets:
                yield self._map_tweet(tweet)
                fetched += 1
                if limit and fetched >= limit:
                    return
            
            cursor = result.get("nextCursor")
            if not cursor:
                break
            
            await asyncio.sleep(random.uniform(5, 10))  # Conservative delay
    
    def _map_tweet(self, tweet: dict) -> PlatformMedia:
        """Convert Twitter API tweet to PlatformMedia."""
        return PlatformMedia(
            platform="twitter",
            item_pk=tweet["id"],
            item_id=None,
            item_code=None,
            category="likes",
            media_type=self._determine_media_type(tweet),
            product_type=None,
            author_username=tweet["author"]["username"],
            author_user_id=tweet.get("authorId"),
            caption=tweet.get("text"),
            post_url=f"https://x.com/{tweet['author']['username']}/status/{tweet['id']}",
            taken_at=self._parse_timestamp(tweet.get("createdAt")),
            resources=self._extract_resources(tweet),
            metadata={
                "replyCount": tweet.get("replyCount"),
                "retweetCount": tweet.get("retweetCount"),
                "likeCount": tweet.get("likeCount"),
            },
        )
```

### Twitter Bookmarks Fetcher

Similar to likes, uses `get_bookmarks()` endpoint.

---

## Telegram Formatting

### Twitter Posts

```
{tweet_text}

{media_indicator}
@{author_username}
https://x.com/{username}/status/{id}
{timestamp}
```

Where media_indicator:
- Photo: (empty or count if multiple)
- Video: (video icon or duration)
- No media: (empty)

---

## Processing Flow

### Parallel Processing

```python
# processor.py

async def process_all_platforms():
    tasks = []
    
    if config.INSTAGRAM_ENABLED:
        tasks.append(process_instagram())
    
    if config.TWITTER_ENABLED:
        tasks.append(process_twitter())
    
    await asyncio.gather(*tasks, return_exceptions=True)

async def process_twitter():
    client = TwitterClient(load_twitter_credentials())
    
    fetchers = [
        TwitterLikesFetcher(client),
        TwitterBookmarksFetcher(client),
    ]
    
    for fetcher in fetchers:
        async for media in fetcher.fetch():
            if await db.is_processed(media.platform, media.item_pk):
                continue
            
            await db.insert_media(media)
            await download_media(media)
            await upload_to_telegram(media, config.TWITTER_CHAT_ID)
            await db.mark_uploaded(media)
```

---

## Rate Limiting (Twitter)

Conservative approach due to X's aggressive anti-bot measures:

| Action | Delay |
|--------|-------|
| Between requests | 5-10 seconds random |
| Between pages | 10-15 seconds |
| On 429 error | 60 seconds + exponential backoff |
| On any error | 30 seconds + exponential backoff |
| Per session | Max 100 requests/hour |

---

## Implementation Phases

### Phase 1: Foundation (refactoring)
1. Rename package to `social_archiver`
2. Create `platforms/` directory structure
3. Move Instagram code to `platforms/instagram/`
4. Create base classes in `platforms/base.py`
5. Create `models/media.py` with `PlatformMedia`
6. Update database schema (migration)
7. Update imports throughout codebase

### Phase 2: Twitter MVP
1. Implement `platforms/twitter/auth.py`
2. Implement `platforms/twitter/client.py` (GraphQL calls)
3. Implement `platforms/twitter/constants.py` (query IDs, features)
4. Implement `platforms/twitter/fetchers/likes.py`
5. Implement `platforms/twitter/fetchers/bookmarks.py`
6. Add Twitter config variables
7. Update processor for parallel execution
8. Update Telegram client for Twitter formatting

### Phase 3: Testing & Polish
1. Test Twitter authentication flow
2. Test likes fetching with pagination
3. Test bookmarks fetching
4. Test Telegram uploads
5. Test rate limiting / backoff
6. Test parallel processing with Instagram
7. Documentation updates

---

## Files to Modify

### Rename/Move
- `insta_archiver/` -> `social_archiver/`
- `insta_archiver/instagram_client.py` -> `social_archiver/platforms/instagram/client.py`
- `insta_archiver/simple_media.py` -> `social_archiver/platforms/instagram/simple_media.py`
- `insta_archiver/fetchers/` -> `social_archiver/platforms/instagram/fetchers/`
- `insta_archiver/telegram_client.py` -> `social_archiver/telegram/client.py`
- `insta_archiver/embedding_*.py` -> `social_archiver/embeddings/`

### Create New
- `social_archiver/platforms/__init__.py`
- `social_archiver/platforms/base.py`
- `social_archiver/platforms/twitter/__init__.py`
- `social_archiver/platforms/twitter/client.py`
- `social_archiver/platforms/twitter/auth.py`
- `social_archiver/platforms/twitter/constants.py`
- `social_archiver/platforms/twitter/features.py`
- `social_archiver/platforms/twitter/fetchers/__init__.py`
- `social_archiver/platforms/twitter/fetchers/likes.py`
- `social_archiver/platforms/twitter/fetchers/bookmarks.py`
- `social_archiver/models/__init__.py`
- `social_archiver/models/media.py`
- `social_archiver/telegram/__init__.py`

### Modify
- `pyproject.toml` - rename package, add httpx dependency
- `social_archiver/config.py` - add Twitter config
- `social_archiver/database.py` - new schema, migration
- `social_archiver/processor.py` - parallel processing
- `social_archiver/telegram/client.py` - Twitter formatting
- `.env.example` - add Twitter variables

---

## Dependencies

### New
```toml
httpx = "^0.27"  # async HTTP client for Twitter API
```

### Existing (unchanged)
- instagrapi
- python-telegram-bot
- aiosqlite
- python-dotenv
- schedule
- pymilvus (optional)

---

## Error Handling

### Twitter-specific Errors

| Error | Response |
|-------|----------|
| 401 Unauthorized | Cookies expired, notify user |
| 403 Forbidden | Account restricted, notify user |
| 404 Not Found | Query ID stale, attempt refresh |
| 429 Rate Limited | Back off 60s, then exponential |
| Network error | Retry 3x with backoff |

---

## Embeddings (Vector Search)

The existing Instagram archiver has an optional embeddings system using:
- VLM (Gemini via OpenRouter) for visual description
- Text embedding model (Qwen3 via OpenRouter) for vector generation
- Milvus for vector storage and similarity search

### Milvus Collections

Current Instagram collections:
- `instagram_likes`
- `instagram_saved`
- `instagram_shared`

New Twitter collections:
- `twitter_likes`
- `twitter_bookmarks`

### Collection Naming Pattern

```python
# milvus_manager.py - updated collection mapping
collections = {
    # Instagram
    ("instagram", "likes"): "instagram_likes",
    ("instagram", "saved"): "instagram_saved",
    ("instagram", "shared"): "instagram_shared",
    # Twitter
    ("twitter", "likes"): "twitter_likes",
    ("twitter", "bookmarks"): "twitter_bookmarks",
}
```

### Twitter Embedding Flow

Same flow as Instagram:
1. Download media (photo/video)
2. Generate VLM description using Gemini
3. Combine tweet text + VLM description
4. Generate text embedding using Qwen3
5. Store in Milvus with metadata

### Twitter-specific Metadata in Milvus

```python
metadata = {
    "caption": tweet_text,           # Tweet text
    "username": author_username,
    "tweet_id": item_pk,
    "retweet_count": int,
    "like_count": int,
    "reply_count": int,
}
```

### EmbeddingProcessor Updates

Current `process_media` signature:
```python
async def process_media(
    self, media_pk: int, media: SimpleMedia, category: str, local_paths: List[Path]
) -> bool
```

New platform-agnostic signature:
```python
async def process_media(
    self,
    platform: str,              # 'instagram' or 'twitter'
    item_pk: str,               # platform ID (string)
    media: PlatformMedia,       # normalized media object
    category: str,
    local_paths: List[Path]
) -> bool
```

### MilvusManager Updates

Current `get_doc_id`:
```python
def get_doc_id(media_pk: int, resource_index: Optional[int] = None) -> int
```

New platform-agnostic signature:
```python
def get_doc_id(platform: str, item_pk: str, resource_index: Optional[int] = None) -> int
```

Hash input changes from `"{media_pk}:{idx_str}"` to `"{platform}:{item_pk}:{idx_str}"`.

### Search Updates

The `search_embeddings.py` CLI needs updates:
- Add `--platform` flag to filter by platform
- Default to searching all collections
- Show platform in results

---

## Open Questions

1. Should we support Twitter bookmark folders?
2. Should Twitter text-only tweets (no media) be archived?
3. Should we download Twitter video variants (quality selection)?
4. What Telegram format for quote tweets?

---

## References

- bird library: `reference/bird/`
- Existing spec: `SPEC.md`
- Twitter GraphQL endpoints: reverse-engineered from bird
