import json
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

import aiosqlite


class ArchiveStatus(StrEnum):
    PENDING = "pending"
    ARCHIVED = "archived"
    TOMBSTONE = "tombstone"
    SKIPPED = "skipped"
    FAILED = "failed"


class StageStatus(StrEnum):
    """Status of the upload and embed stages, each independent of the others."""

    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True)
class Item:
    """One archived post. The three stage statuses drive the independent
    archive/upload/embed jobs; each job selects by its own status only."""

    item_id: str
    platform: str
    category: str
    author_username: str
    post_url: str
    author_id: str | None = None
    text: str | None = None
    is_article: bool = False
    created_at: datetime | None = None

    has_media: bool = False
    media_count: int = 0
    media_types: list[str] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)

    # Reply/quote/retweet graph; NULL for platforms without one
    conversation_id: str | None = None
    in_reply_to_status_id: str | None = None
    quoted_tweet_id: str | None = None
    retweeted_tweet_id: str | None = None
    is_retweet: bool = False
    origin: str | None = None
    discovered_via_item_id: str | None = None
    thread_position: str | None = None
    has_self_replies: bool | None = None
    thread_root_id: str | None = None

    reply_count: int | None = None
    retweet_count: int | None = None
    like_count: int | None = None
    quote_count: int | None = None
    bookmark_count: int | None = None
    view_count: int | None = None

    # Instagram-specific
    collection_name: str | None = None
    shared_by_username: str | None = None
    product_type: str | None = None

    # Reddit-specific
    subreddit: str | None = None
    link_url: str | None = None  # a link post's outbound target, which post_url is not

    archive_status: ArchiveStatus = ArchiveStatus.PENDING
    upload_status: StageStatus = StageStatus.PENDING
    embed_status: StageStatus = StageStatus.PENDING
    archive_error: str | None = None
    upload_error: str | None = None
    embed_error: str | None = None
    archive_attempts: int = 0

    local_paths: list[Path] = field(default_factory=list)
    telegram_message_ids: list[int] = field(default_factory=list)
    vlm_description: str | None = None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Self:
        created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
        return cls(
            item_id=row["item_id"],
            platform=row["platform"],
            category=row["category"],
            author_username=row["author_username"],
            post_url=row["post_url"],
            author_id=row["author_id"],
            text=row["text"],
            is_article=bool(row["is_article"]),
            created_at=created_at,
            has_media=bool(row["has_media"]),
            media_count=row["media_count"],
            media_types=json.loads(row["media_types"] or "[]"),
            media_urls=json.loads(row["media_urls"] or "[]"),
            conversation_id=row["conversation_id"],
            in_reply_to_status_id=row["in_reply_to_status_id"],
            quoted_tweet_id=row["quoted_tweet_id"],
            retweeted_tweet_id=row["retweeted_tweet_id"],
            is_retweet=bool(row["is_retweet"]),
            origin=row["origin"],
            discovered_via_item_id=row["discovered_via_item_id"],
            thread_position=row["thread_position"],
            has_self_replies=None if row["has_self_replies"] is None else bool(row["has_self_replies"]),
            thread_root_id=row["thread_root_id"],
            reply_count=row["reply_count"],
            retweet_count=row["retweet_count"],
            like_count=row["like_count"],
            quote_count=row["quote_count"],
            bookmark_count=row["bookmark_count"],
            view_count=row["view_count"],
            collection_name=row["collection_name"],
            shared_by_username=row["shared_by_username"],
            product_type=row["product_type"],
            subreddit=row["subreddit"],
            link_url=row["link_url"],
            archive_status=ArchiveStatus(row["archive_status"]),
            upload_status=StageStatus(row["upload_status"]),
            embed_status=StageStatus(row["embed_status"]),
            archive_error=row["archive_error"],
            upload_error=row["upload_error"],
            embed_error=row["embed_error"],
            archive_attempts=row["archive_attempts"],
            local_paths=[Path(p) for p in json.loads(row["local_paths"] or "[]")],
            telegram_message_ids=json.loads(row["telegram_message_ids"] or "[]"),
            vlm_description=row["vlm_description"],
        )


_ITEM_COLUMNS = [f.name for f in fields(Item)]


def _to_column(item: Item, name: str) -> str | int | float | None:
    value = getattr(item, name)
    match value:
        case bool():
            return int(value)
        case list():
            return json.dumps(value, default=str) if value else None
        case datetime():
            return value.isoformat()
        case _:
            return value


class Database:
    """Unified archive database — one `items` row per archived post, shared
    schema across platforms. Each platform instance points at its own file."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection = None  # type: ignore

    async def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._init_schema()

    async def close(self):
        if self._connection:
            await self._connection.close()

    async def _init_schema(self):
        await self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                category TEXT NOT NULL,
                author_username TEXT NOT NULL,
                post_url TEXT NOT NULL,
                author_id TEXT,
                text TEXT,
                is_article INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP,

                has_media INTEGER NOT NULL DEFAULT 0,
                media_count INTEGER NOT NULL DEFAULT 0,
                media_types TEXT,
                media_urls TEXT,

                -- reply/quote/retweet graph; NULL for platforms without one
                conversation_id TEXT,
                in_reply_to_status_id TEXT,
                quoted_tweet_id TEXT,
                retweeted_tweet_id TEXT,
                is_retweet INTEGER NOT NULL DEFAULT 0,
                origin TEXT,
                discovered_via_item_id TEXT,
                thread_position TEXT,
                has_self_replies INTEGER,
                thread_root_id TEXT,

                reply_count INTEGER,
                retweet_count INTEGER,
                like_count INTEGER,
                quote_count INTEGER,
                bookmark_count INTEGER,
                view_count INTEGER,

                collection_name TEXT,
                shared_by_username TEXT,
                product_type TEXT,
                subreddit TEXT,
                link_url TEXT,

                archive_status TEXT NOT NULL DEFAULT 'pending',
                upload_status TEXT NOT NULL DEFAULT 'pending',
                embed_status TEXT NOT NULL DEFAULT 'pending',
                archive_error TEXT,
                upload_error TEXT,
                embed_error TEXT,
                archive_attempts INTEGER NOT NULL DEFAULT 0,

                local_paths TEXT,
                telegram_message_ids TEXT,
                vlm_description TEXT,

                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                archived_at TIMESTAMP,
                uploaded_at TIMESTAMP,
                embedded_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_items_platform_category ON items(platform, category);
            CREATE INDEX IF NOT EXISTS idx_items_archive_status ON items(platform, archive_status);
            CREATE INDEX IF NOT EXISTS idx_items_upload_status ON items(platform, upload_status);
            CREATE INDEX IF NOT EXISTS idx_items_embed_status ON items(platform, embed_status);
            CREATE INDEX IF NOT EXISTS idx_items_conversation_id ON items(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_items_origin ON items(origin);
            CREATE INDEX IF NOT EXISTS idx_items_author ON items(author_username);
            CREATE INDEX IF NOT EXISTS idx_items_thread_root ON items(thread_root_id);
            CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at);

            -- An item can belong to several categories at once (a post you wrote is also
            -- one you upvoted), which items.category alone cannot express: it only records
            -- whichever pass inserted the row first.
            CREATE TABLE IF NOT EXISTS item_categories (
                item_id TEXT NOT NULL,
                category TEXT NOT NULL,
                PRIMARY KEY (item_id, category)
            );
            CREATE INDEX IF NOT EXISTS idx_item_categories_category ON item_categories(category);

            -- A saved post can sit in several Instagram collections at once, and can be moved
            -- between them later. items.collection_name records only where it was first seen.
            CREATE TABLE IF NOT EXISTS item_collections (
                item_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                PRIMARY KEY (item_id, collection)
            );
            CREATE INDEX IF NOT EXISTS idx_item_collections_collection ON item_collections(collection);

            -- Where a history walk had got to, so an interrupted one resumes instead of
            -- paging from the top again. Only a full backfill writes here.
            CREATE TABLE IF NOT EXISTS fetch_cursors (
                platform TEXT NOT NULL,
                category TEXT NOT NULL,
                cursor TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (platform, category)
            );

            -- Conversations thread discovery has already walked. Pairs are rederived from the
            -- whole archive every run, so without this a repeat run researches what it has.
            CREATE TABLE IF NOT EXISTS searched_conversations (
                platform TEXT NOT NULL,
                author TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                searched_at TIMESTAMP NOT NULL,
                PRIMARY KEY (platform, author, conversation_id)
            );
        """)
        await self._connection.commit()

    async def get_cursor(self, platform: str, category: str) -> str:
        cursor = await self._connection.execute(
            "SELECT cursor FROM fetch_cursors WHERE platform = ? AND category = ?", (platform, category)
        )
        row = await cursor.fetchone()
        return row["cursor"] if row else ""

    async def set_cursor(self, platform: str, category: str, cursor: str):
        """Call only after the page's items are committed. In that order a crash costs one
        refetched page, which INSERT OR IGNORE absorbs; the reverse skips a page for good."""
        await self._connection.execute(
            """
            INSERT INTO fetch_cursors (platform, category, cursor, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(platform, category) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
            """,
            (platform, category, cursor, datetime.now().isoformat()),
        )
        await self._connection.commit()

    async def clear_cursor(self, platform: str, category: str):
        """A walk that reached the end has nothing to resume from, so the next full run
        starts from the top rather than replaying the last page forever."""
        await self._connection.execute(
            "DELETE FROM fetch_cursors WHERE platform = ? AND category = ?", (platform, category)
        )
        await self._connection.commit()

    async def searched_conversations(self, platform: str, since: datetime) -> set[tuple[str, str]]:
        cursor = await self._connection.execute(
            "SELECT author, conversation_id FROM searched_conversations WHERE platform = ? AND searched_at > ?",
            (platform, since.isoformat()),
        )
        return {(row["author"], row["conversation_id"]) for row in await cursor.fetchall()}

    async def mark_searched(self, platform: str, pairs: Iterable[tuple[str, str]]):
        """Call only after the tweets the search returned are committed. In that order a crash
        costs a repeated search; the reverse drops a conversation nothing will look at again."""
        now = datetime.now().isoformat()
        await self._connection.executemany(
            """
            INSERT INTO searched_conversations (platform, author, conversation_id, searched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(platform, author, conversation_id) DO UPDATE SET searched_at = excluded.searched_at
            """,
            [(platform, author, conversation_id, now) for author, conversation_id in pairs],
        )
        await self._connection.commit()

    async def insert(self, item: Item):
        columns = ", ".join(_ITEM_COLUMNS)
        placeholders = ", ".join("?" * len(_ITEM_COLUMNS))
        await self._connection.execute(
            f"INSERT OR IGNORE INTO items ({columns}) VALUES ({placeholders})",
            [_to_column(item, name) for name in _ITEM_COLUMNS],
        )
        await self._connection.execute(
            "INSERT OR IGNORE INTO item_categories (item_id, category) VALUES (?, ?)",
            (item.item_id, item.category),
        )
        await self._connection.commit()

    async def add_categories(self, pairs: Iterable[tuple[str, str]]):
        """Record category membership for items that already exist, which is how an item
        archived under one category gets attributed to the others it also belongs to."""
        await self._connection.executemany(
            "INSERT OR IGNORE INTO item_categories (item_id, category) VALUES (?, ?)", list(pairs)
        )
        await self._connection.commit()

    async def add_collections(self, pairs: Iterable[tuple[str, str]]):
        """Record collection membership for every item seen, archived or not. The archive loop
        skips items it already has, so membership recorded only on insert would freeze at
        whichever collection the item was first found in."""
        await self._connection.executemany(
            "INSERT OR IGNORE INTO item_collections (item_id, collection) VALUES (?, ?)", list(pairs)
        )
        await self._connection.commit()

    async def collections_for(self, item_id: str) -> set[str]:
        cursor = await self._connection.execute(
            "SELECT collection FROM item_collections WHERE item_id = ?", (item_id,)
        )
        return {row["collection"] for row in await cursor.fetchall()}

    async def categories_for(self, item_id: str) -> set[str]:
        cursor = await self._connection.execute("SELECT category FROM item_categories WHERE item_id = ?", (item_id,))
        return {row["category"] for row in await cursor.fetchall()}

    async def get(self, item_id: str) -> Item | None:
        cursor = await self._connection.execute("SELECT * FROM items WHERE item_id = ?", (item_id,))
        row = await cursor.fetchone()
        return Item.from_row(row) if row else None

    async def all_ids(self, platform: str) -> set[str]:
        cursor = await self._connection.execute("SELECT item_id FROM items WHERE platform = ?", (platform,))
        return {row["item_id"] for row in await cursor.fetchall()}

    async def ids_by_origin(self, platform: str, origin: str) -> set[str]:
        cursor = await self._connection.execute(
            "SELECT item_id FROM items WHERE platform = ? AND origin = ?",
            (platform, origin),
        )
        return {row["item_id"] for row in await cursor.fetchall()}

    async def upgrade_origin(self, item_id: str, origin: str) -> bool:
        """Promote a context-discovered item (thread/parent/quoted/etc.) to a seed
        origin like 'liked' when the user later likes/bookmarks it directly.
        Never demotes an existing seed origin."""
        cursor = await self._connection.execute(
            """
            UPDATE items SET origin = ?
            WHERE item_id = ? AND origin IN
                ('thread', 'reply', 'conversation', 'parent', 'quoted', 'linked', 'retweet',
                 'liked_reply', 'submission')
            """,
            (origin, item_id),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def _select_items(self, query: str, params: Iterable) -> list[Item]:
        cursor = await self._connection.execute(query, tuple(params))
        return [Item.from_row(row) for row in await cursor.fetchall()]

    async def pending_archive(self, platform: str, include_failed: bool) -> list[Item]:
        statuses = "('pending', 'failed')" if include_failed else "('pending')"
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND archive_status IN {statuses}
            ORDER BY fetched_at
            """,
            (platform,),
        )

    async def _pending_stage(self, stage: str, platform: str, category: str, include_failed: bool) -> list[Item]:
        statuses = "('pending', 'failed')" if include_failed else "('pending')"
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND category = ? AND archive_status = 'archived' AND {stage} IN {statuses}
            ORDER BY created_at
            """,
            (platform, category),
        )

    async def pending_upload(self, platform: str, category: str, include_failed: bool = False) -> list[Item]:
        return await self._pending_stage("upload_status", platform, category, include_failed)

    async def pending_embed(self, platform: str, category: str, include_failed: bool = False) -> list[Item]:
        return await self._pending_stage("embed_status", platform, category, include_failed)

    async def thread_members(self, platform: str, root_ids: set[str]) -> list[Item]:
        """All archived items belonging to the given thread roots (the roots
        themselves included), for VLM context reconstruction."""
        if not root_ids:
            return []
        placeholders = ", ".join("?" * len(root_ids))
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND archive_status = 'archived'
            AND (thread_root_id IN ({placeholders}) OR item_id IN ({placeholders}))
            ORDER BY created_at
            """,
            (platform, *root_ids, *root_ids),
        )

    async def cleanup_candidates(self, platform: str, require_embed: bool) -> list[Item]:
        """Items whose downloads every enabled consumer has finished with."""
        embed_filter = "AND embed_status IN ('done', 'skipped')" if require_embed else ""
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND local_paths IS NOT NULL
            AND upload_status IN ('done', 'skipped') {embed_filter}
            """,
            (platform,),
        )

    async def tombstoned(self, platform: str) -> list[Item]:
        return await self._select_items(
            "SELECT * FROM items WHERE platform = ? AND archive_status = 'tombstone' ORDER BY fetched_at",
            (platform,),
        )

    async def replace_tombstone(self, item: Item) -> bool:
        """A tombstone that resolves on a later attempt becomes the real thing.

        Guarded on the status so a placeholder can be filled in but a genuinely archived item is
        never overwritten by a thinner copy. How the item was found is kept: that came from the
        run that discovered it, not from this lookup."""
        preserved = {"item_id", "platform", "category", "origin", "discovered_via_item_id", "fetched_at"}
        columns = [c for c in _ITEM_COLUMNS if c not in preserved]
        assignments = ", ".join(f"{c} = ?" for c in columns)
        cursor = await self._connection.execute(
            f"UPDATE items SET {assignments} WHERE item_id = ? AND archive_status = 'tombstone'",
            [_to_column(item, name) for name in columns] + [item.item_id],
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def set_tombstone_reason(self, item_id: str, reason: str) -> bool:
        cursor = await self._connection.execute(
            "UPDATE items SET text = ? WHERE item_id = ? AND archive_status = 'tombstone'",
            (reason, item_id),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def mark_archived(self, item_id: str, local_paths: list[Path]):
        await self._connection.execute(
            """
            UPDATE items
            SET archive_status = 'archived', archived_at = ?, local_paths = ?,
                archive_error = NULL, archive_attempts = 0
            WHERE item_id = ?
            """,
            (
                datetime.now().isoformat(),
                json.dumps([str(p) for p in local_paths]) if local_paths else None,
                item_id,
            ),
        )
        await self._connection.commit()

    async def mark_archive_failed(self, item_id: str, error: str):
        await self._connection.execute(
            """
            UPDATE items
            SET archive_status = 'failed', archive_error = ?, archive_attempts = archive_attempts + 1
            WHERE item_id = ?
            """,
            (error, item_id),
        )
        await self._connection.commit()

    async def failed_archive(self, platform: str) -> list[Item]:
        return await self._select_items(
            "SELECT * FROM items WHERE platform = ? AND archive_status = 'failed' ORDER BY fetched_at",
            (platform,),
        )

    async def refresh_media(self, item_id: str, urls: list[str], kinds: list[str], count: int) -> bool:
        """Replace an item's stored media urls. `ensure_media` only ever retries the urls
        already on the row, so a wrong or expired one can never recover without this."""
        cursor = await self._connection.execute(
            """
            UPDATE items SET media_urls = ?, media_types = ?, media_count = ?, has_media = ?
            WHERE item_id = ? AND media_urls IS NOT ?
            """,
            (
                json.dumps(urls) if urls else None,
                json.dumps(kinds) if kinds else None,
                count,
                int(bool(urls)),
                item_id,
                json.dumps(urls) if urls else None,
            ),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def mark_uploaded(self, item_id: str, message_ids: list[int]):
        await self._connection.execute(
            """
            UPDATE items SET upload_status = 'done', uploaded_at = ?, telegram_message_ids = ?, upload_error = NULL
            WHERE item_id = ?
            """,
            (datetime.now().isoformat(), json.dumps(message_ids), item_id),
        )
        await self._connection.commit()

    async def mark_upload_failed(self, item_id: str, error: str):
        await self._connection.execute(
            "UPDATE items SET upload_status = 'failed', upload_error = ? WHERE item_id = ?",
            (error, item_id),
        )
        await self._connection.commit()

    async def mark_embedded(self, item_id: str, vlm_description: str | None = None):
        await self._connection.execute(
            """
            UPDATE items SET embed_status = 'done', embedded_at = ?, vlm_description = ?, embed_error = NULL
            WHERE item_id = ?
            """,
            (datetime.now().isoformat(), vlm_description, item_id),
        )
        await self._connection.commit()

    async def mark_embed_failed(self, item_id: str, error: str):
        await self._connection.execute(
            "UPDATE items SET embed_status = 'failed', embed_error = ? WHERE item_id = ?",
            (error, item_id),
        )
        await self._connection.commit()

    async def clear_local_paths(self, item_id: str):
        await self._connection.execute("UPDATE items SET local_paths = NULL WHERE item_id = ?", (item_id,))
        await self._connection.commit()

    async def stats(self, platform: str) -> dict[str, dict[str, int]]:
        cursor = await self._connection.execute(
            """
            SELECT category, COUNT(*) AS total,
                   SUM(archive_status = 'archived') AS archived,
                   SUM(upload_status = 'done') AS uploaded,
                   SUM(embed_status = 'done') AS embedded
            FROM items WHERE platform = ? GROUP BY category
            """,
            (platform,),
        )
        return {
            row["category"]: {
                "total": row["total"],
                "archived": row["archived"],
                "uploaded": row["uploaded"],
                "embedded": row["embedded"],
            }
            for row in await cursor.fetchall()
        }

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
