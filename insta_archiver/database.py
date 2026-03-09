import json
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from insta_archiver import config


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
            CREATE TABLE IF NOT EXISTS processed_media (
                media_pk INTEGER PRIMARY KEY,
                media_id TEXT NOT NULL,
                media_code TEXT NOT NULL,
                category TEXT NOT NULL,
                media_type INTEGER NOT NULL,
                product_type TEXT,
                author_username TEXT NOT NULL,
                author_user_id INTEGER NOT NULL,
                caption TEXT,
                post_url TEXT NOT NULL,
                taken_at TIMESTAMP NOT NULL,
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

    async def is_processed(self, media_pk: int) -> bool:
        cursor = await self._connection.execute(
            "SELECT 1 FROM processed_media WHERE media_pk = ?", (media_pk,)
        )
        return await cursor.fetchone() is not None

    async def insert_media(
        self,
        media_pk: int,
        media_id: str,
        media_code: str,
        category: str,
        media_type: int,
        product_type: Optional[str],
        author_username: str,
        author_user_id: int,
        caption: Optional[str],
        post_url: str,
        taken_at: datetime,
        status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        metadata_json = json.dumps(metadata) if metadata else None
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO processed_media 
            (media_pk, media_id, media_code, category, media_type, product_type,
             author_username, author_user_id, caption, post_url, taken_at, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_pk,
                media_id,
                media_code,
                category,
                media_type,
                product_type,
                author_username,
                author_user_id,
                caption,
                post_url,
                taken_at,
                status,
                metadata_json,
            ),
        )
        await self._connection.commit()

    async def update_status(
        self, media_pk: int, status: str, error_message: Optional[str] = None
    ):
        await self._connection.execute(
            "UPDATE processed_media SET status = ?, error_message = ? WHERE media_pk = ?",
            (status, error_message, media_pk),
        )
        await self._connection.commit()

    async def mark_downloaded(self, media_pk: int, local_paths: List[str]):
        await self._connection.execute(
            """
            UPDATE processed_media 
            SET status = 'downloaded', downloaded_at = ?, local_paths = ?
            WHERE media_pk = ?
            """,
            (datetime.now(), json.dumps(local_paths), media_pk),
        )
        await self._connection.commit()

    async def mark_uploaded(self, media_pk: int, telegram_message_ids: List[int]):
        await self._connection.execute(
            """
            UPDATE processed_media 
            SET status = 'uploaded', uploaded_at = ?, telegram_message_ids = ?
            WHERE media_pk = ?
            """,
            (datetime.now(), json.dumps(telegram_message_ids), media_pk),
        )
        await self._connection.commit()

    async def get_pending_media(self) -> List[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM processed_media WHERE status IN ('pending', 'downloaded') ORDER BY fetched_at"
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
            FROM processed_media
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
        self, media_pk: int, success: bool = True, error_message: Optional[str] = None,
        vlm_description: Optional[str] = None
    ):
        """Mark embedding generation as completed or failed"""
        status = "completed" if success else "failed"
        await self._connection.execute(
            """
            UPDATE processed_media 
            SET embedding_status = ?, embedded_at = ?, error_message = ?, vlm_description = ?
            WHERE media_pk = ?
            """,
            (status, datetime.now() if success else None, error_message, vlm_description, media_pk),
        )
        await self._connection.commit()

    async def get_media_without_embeddings(
        self, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get media that have been uploaded but don't have embeddings yet"""
        query = """
            SELECT * FROM processed_media 
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

    async def get_failed_large_files(
        self, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get media that failed due to file size limits.

        Args:
            category: Optional filter by category (e.g. 'saved', 'likes')

        Returns:
            List of media records that failed with 'too large' errors
        """
        query = """
            SELECT * FROM processed_media 
            WHERE status = 'failed' 
            AND (error_message LIKE '%too large%' 
                 OR error_message LIKE '%Too Large%'
                 OR error_message LIKE '%exceeds%')
        """
        params = []

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY fetched_at DESC"

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
