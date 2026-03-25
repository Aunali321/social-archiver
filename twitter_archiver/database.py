import json
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from twitter_archiver import config


class Database:
    def __init__(self, db_path: Path = config.DATABASE_PATH):
        self.db_path = db_path
        self._connection: aiosqlite.Connection = None  # type: ignore

    async def connect(self):
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._init_schema()

    async def close(self):
        if self._connection:
            await self._connection.close()

    async def _init_schema(self):
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS processed_tweets (
                tweet_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                author_username TEXT NOT NULL,
                author_id TEXT,
                tweet_text TEXT,
                post_url TEXT NOT NULL,
                has_media INTEGER NOT NULL DEFAULT 0,
                media_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                downloaded_at TIMESTAMP,
                uploaded_at TIMESTAMP,
                embedded_at TIMESTAMP,
                status TEXT NOT NULL,
                embedding_status TEXT,
                error_message TEXT,
                local_paths TEXT,
                telegram_message_ids TEXT,
                metadata TEXT,
                vlm_description TEXT
            )
        """)
        await self._connection.commit()

    async def is_processed(self, tweet_id: str) -> bool:
        cursor = await self._connection.execute(
            "SELECT 1 FROM processed_tweets WHERE tweet_id = ?", (tweet_id,)
        )
        return await cursor.fetchone() is not None

    async def insert_tweet(
        self,
        tweet_id: str,
        category: str,
        author_username: str,
        author_id: Optional[str],
        tweet_text: Optional[str],
        post_url: str,
        has_media: bool,
        media_count: int,
        created_at: Optional[datetime],
        status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        metadata_json = json.dumps(metadata) if metadata else None
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO processed_tweets
            (tweet_id, category, author_username, author_id, tweet_text,
             post_url, has_media, media_count, created_at, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tweet_id,
                category,
                author_username,
                author_id,
                tweet_text,
                post_url,
                1 if has_media else 0,
                media_count,
                created_at,
                status,
                metadata_json,
            ),
        )
        await self._connection.commit()

    async def update_status(
        self, tweet_id: str, status: str, error_message: Optional[str] = None
    ):
        await self._connection.execute(
            "UPDATE processed_tweets SET status = ?, error_message = ? WHERE tweet_id = ?",
            (status, error_message, tweet_id),
        )
        await self._connection.commit()

    async def mark_downloaded(self, tweet_id: str, local_paths: List[str]):
        await self._connection.execute(
            """
            UPDATE processed_tweets
            SET status = 'downloaded', downloaded_at = ?, local_paths = ?
            WHERE tweet_id = ?
            """,
            (datetime.now(), json.dumps(local_paths), tweet_id),
        )
        await self._connection.commit()

    async def mark_uploaded(self, tweet_id: str, telegram_message_ids: List[int]):
        await self._connection.execute(
            """
            UPDATE processed_tweets
            SET status = 'uploaded', uploaded_at = ?, telegram_message_ids = ?
            WHERE tweet_id = ?
            """,
            (datetime.now(), json.dumps(telegram_message_ids), tweet_id),
        )
        await self._connection.commit()

    async def get_pending_tweets(self) -> List[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM processed_tweets WHERE status IN ('pending', 'downloaded') ORDER BY fetched_at"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_stats(self) -> Dict[str, Dict[str, int]]:
        cursor = await self._connection.execute(
            """
            SELECT
                category,
                COUNT(*) as total,
                SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) as uploaded,
                SUM(CASE WHEN embedding_status = 'completed' THEN 1 ELSE 0 END) as embedded
            FROM processed_tweets
            GROUP BY category
            """
        )
        rows = await cursor.fetchall()
        return {
            row["category"]: {
                "total": row["total"],
                "uploaded": row["uploaded"],
                "embedded": row["embedded"],
            }
            for row in rows
        }

    async def mark_embedded(
        self, tweet_id: str, success: bool = True, error_message: Optional[str] = None,
        vlm_description: Optional[str] = None
    ):
        status = "completed" if success else "failed"
        await self._connection.execute(
            """
            UPDATE processed_tweets
            SET embedding_status = ?, embedded_at = ?, error_message = ?, vlm_description = ?
            WHERE tweet_id = ?
            """,
            (status, datetime.now() if success else None, error_message, vlm_description, tweet_id),
        )
        await self._connection.commit()

    async def get_tweets_without_embeddings(
        self, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT * FROM processed_tweets
            WHERE status = 'uploaded'
            AND (embedding_status IS NULL OR embedding_status = 'failed')
        """
        params = []

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY uploaded_at DESC"

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
