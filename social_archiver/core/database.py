import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite


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
                author_id TEXT,
                text TEXT,
                post_url TEXT NOT NULL,
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

                status TEXT NOT NULL DEFAULT 'pending',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                downloaded_at TIMESTAMP,
                uploaded_at TIMESTAMP,
                embedded_at TIMESTAMP,
                embedding_status TEXT,
                vlm_description TEXT,

                local_paths TEXT,
                telegram_message_ids TEXT,
                error_message TEXT,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_items_platform_category ON items(platform, category);
            CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
            CREATE INDEX IF NOT EXISTS idx_items_conversation_id ON items(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_items_in_reply_to ON items(in_reply_to_status_id);
            CREATE INDEX IF NOT EXISTS idx_items_quoted ON items(quoted_tweet_id);
            CREATE INDEX IF NOT EXISTS idx_items_retweeted ON items(retweeted_tweet_id);
            CREATE INDEX IF NOT EXISTS idx_items_origin ON items(origin);
            CREATE INDEX IF NOT EXISTS idx_items_author ON items(author_username);
            CREATE INDEX IF NOT EXISTS idx_items_discovered_via ON items(discovered_via_item_id);
            CREATE INDEX IF NOT EXISTS idx_items_thread_root ON items(thread_root_id);
            CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at);
        """)
        await self._connection.commit()

    async def is_processed(self, platform: str, item_id: str) -> bool:
        cursor = await self._connection.execute(
            "SELECT 1 FROM items WHERE platform = ? AND item_id = ?", (platform, item_id)
        )
        return await cursor.fetchone() is not None

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        cursor = await self._connection.execute("SELECT * FROM items WHERE item_id = ?", (item_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_item_ids(self, platform: str) -> set[str]:
        cursor = await self._connection.execute("SELECT item_id FROM items WHERE platform = ?", (platform,))
        rows = await cursor.fetchall()
        return {row["item_id"] for row in rows}

    async def get_item_ids_by_origin(self, platform: str, origin: str) -> set[str]:
        cursor = await self._connection.execute(
            "SELECT item_id FROM items WHERE platform = ? AND origin = ?", (platform, origin)
        )
        rows = await cursor.fetchall()
        return {row["item_id"] for row in rows}

    async def insert_item(
        self,
        item_id: str,
        platform: str,
        category: str,
        author_username: str,
        post_url: str,
        author_id: str | None = None,
        text: str | None = None,
        created_at: datetime | None = None,
        has_media: bool = False,
        media_count: int = 0,
        media_types: list[str] | None = None,
        media_urls: list[str] | None = None,
        conversation_id: str | None = None,
        in_reply_to_status_id: str | None = None,
        quoted_tweet_id: str | None = None,
        retweeted_tweet_id: str | None = None,
        is_retweet: bool = False,
        origin: str | None = None,
        discovered_via_item_id: str | None = None,
        thread_position: str | None = None,
        has_self_replies: bool | None = None,
        thread_root_id: str | None = None,
        reply_count: int | None = None,
        retweet_count: int | None = None,
        like_count: int | None = None,
        quote_count: int | None = None,
        bookmark_count: int | None = None,
        view_count: int | None = None,
        status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ):
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO items
            (item_id, platform, category, author_username, author_id, text, post_url,
             created_at, has_media, media_count, media_types, media_urls,
             conversation_id, in_reply_to_status_id, quoted_tweet_id, retweeted_tweet_id,
             is_retweet, origin, discovered_via_item_id, thread_position, has_self_replies,
             thread_root_id, reply_count, retweet_count, like_count, quote_count,
             bookmark_count, view_count, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id, platform, category, author_username, author_id, text, post_url,
                created_at, 1 if has_media else 0, media_count,
                json.dumps(media_types) if media_types else None,
                json.dumps(media_urls) if media_urls else None,
                conversation_id, in_reply_to_status_id, quoted_tweet_id, retweeted_tweet_id,
                1 if is_retweet else 0, origin, discovered_via_item_id, thread_position,
                None if has_self_replies is None else int(has_self_replies),
                thread_root_id, reply_count, retweet_count, like_count, quote_count,
                bookmark_count, view_count, status,
                json.dumps(metadata) if metadata else None,
            ),
        )
        await self._connection.commit()

    async def upgrade_origin(self, item_id: str, origin: str) -> bool:
        """Promote a context-discovered item (thread/parent/quoted/etc.) to a seed
        origin like 'liked' when the user later likes/bookmarks it directly.
        Never demotes an existing seed origin."""
        cursor = await self._connection.execute(
            """
            UPDATE items SET origin = ?
            WHERE item_id = ? AND origin IN ('thread', 'parent', 'quoted', 'linked', 'retweet', 'liked_reply')
            """,
            (origin, item_id),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def update_status(self, item_id: str, status: str, error_message: str | None = None):
        await self._connection.execute(
            "UPDATE items SET status = ?, error_message = ? WHERE item_id = ?",
            (status, error_message, item_id),
        )
        await self._connection.commit()

    async def mark_downloaded(self, item_id: str, local_paths: list[str]):
        await self._connection.execute(
            "UPDATE items SET status = 'downloaded', downloaded_at = ?, local_paths = ? WHERE item_id = ?",
            (datetime.now(), json.dumps(local_paths), item_id),
        )
        await self._connection.commit()

    async def mark_uploaded(self, item_id: str, telegram_message_ids: list[int]):
        await self._connection.execute(
            "UPDATE items SET status = 'uploaded', uploaded_at = ?, telegram_message_ids = ? WHERE item_id = ?",
            (datetime.now(), json.dumps(telegram_message_ids), item_id),
        )
        await self._connection.commit()

    async def mark_embedded(
        self,
        item_id: str,
        success: bool = True,
        error_message: str | None = None,
        vlm_description: str | None = None,
    ):
        status = "completed" if success else "failed"
        await self._connection.execute(
            """
            UPDATE items
            SET embedding_status = ?, embedded_at = ?, error_message = ?, vlm_description = ?
            WHERE item_id = ?
            """,
            (status, datetime.now() if success else None, error_message, vlm_description, item_id),
        )
        await self._connection.commit()

    async def get_pending_items(self, platform: str) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM items WHERE platform = ? AND status IN ('pending', 'downloaded') ORDER BY fetched_at",
            (platform,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_items_without_embeddings(
        self, platform: str, category: str | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM items
            WHERE platform = ? AND status = 'uploaded'
            AND (embedding_status IS NULL OR embedding_status = 'failed')
        """
        params: list[Any] = [platform]
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY uploaded_at DESC"

        cursor = await self._connection.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_failed_large_files(
        self, platform: str, category: str | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM items
            WHERE platform = ? AND status = 'failed'
            AND (error_message LIKE '%too large%' OR error_message LIKE '%Too Large%' OR error_message LIKE '%exceeds%')
        """
        params: list[Any] = [platform]
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY fetched_at DESC"

        cursor = await self._connection.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_stats(self, platform: str) -> dict[str, dict[str, int]]:
        cursor = await self._connection.execute(
            """
            SELECT category, COUNT(*) as total,
                   SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) as uploaded,
                   SUM(CASE WHEN embedding_status = 'completed' THEN 1 ELSE 0 END) as embedded
            FROM items WHERE platform = ? GROUP BY category
            """,
            (platform,),
        )
        rows = await cursor.fetchall()
        return {
            row["category"]: {"total": row["total"], "uploaded": row["uploaded"], "embedded": row["embedded"]}
            for row in rows
        }

    async def get_items_by_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM items WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_thread(self, thread_root_id: str) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM items WHERE thread_root_id = ? ORDER BY created_at", (thread_root_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_replies_to(self, item_id: str) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM items WHERE in_reply_to_status_id = ? ORDER BY created_at", (item_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
