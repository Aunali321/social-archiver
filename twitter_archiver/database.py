import json
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
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
        await self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS tweets (
                tweet_id TEXT PRIMARY KEY,
                author_username TEXT NOT NULL,
                author_name TEXT,
                author_id TEXT,
                tweet_text TEXT,
                post_url TEXT NOT NULL,
                created_at TIMESTAMP,

                -- Relationship fields (for reconstructing Twitter frontend view)
                conversation_id TEXT,
                in_reply_to_status_id TEXT,
                quoted_tweet_id TEXT,
                retweeted_tweet_id TEXT,
                is_retweet INTEGER NOT NULL DEFAULT 0,

                -- Engagement counts
                reply_count INTEGER,
                retweet_count INTEGER,
                like_count INTEGER,
                quote_count INTEGER,
                bookmark_count INTEGER,
                view_count INTEGER,

                -- Media
                has_media INTEGER NOT NULL DEFAULT 0,
                media_count INTEGER NOT NULL DEFAULT 0,
                media_types TEXT,  -- JSON array of media types e.g. ["photo","video"]
                media_urls TEXT,   -- JSON array of original media URLs (photo :orig / video mp4)

                -- Origin tracking: why was this tweet saved?
                -- liked: user directly liked this tweet
                -- thread: discovered as part of a self-reply chain
                -- parent: discovered by walking up reply chain
                -- quoted: discovered as a quoted tweet
                -- liked_reply: a reply the user liked, found during expansion
                -- retweet: discovered as the original tweet behind a retweet
                origin TEXT NOT NULL,

                -- Which liked tweet caused this to be discovered (NULL if liked directly)
                discovered_via_tweet_id TEXT,

                -- Thread metadata
                thread_position TEXT,  -- root, middle, end, standalone
                has_self_replies INTEGER,
                thread_root_id TEXT,

                -- Processing status
                status TEXT NOT NULL DEFAULT 'pending',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                downloaded_at TIMESTAMP,
                uploaded_at TIMESTAMP,
                embedded_at TIMESTAMP,

                -- Embedding
                embedding_status TEXT,
                vlm_description TEXT,

                -- Storage
                local_paths TEXT,  -- JSON array of downloaded file paths
                telegram_message_ids TEXT,  -- JSON array of telegram message IDs
                error_message TEXT,
                metadata TEXT,  -- JSON for any extra data

                FOREIGN KEY (in_reply_to_status_id) REFERENCES tweets(tweet_id),
                FOREIGN KEY (quoted_tweet_id) REFERENCES tweets(tweet_id),
                FOREIGN KEY (retweeted_tweet_id) REFERENCES tweets(tweet_id),
                FOREIGN KEY (discovered_via_tweet_id) REFERENCES tweets(tweet_id)
            );

            -- Indexes for efficient querying
            CREATE INDEX IF NOT EXISTS idx_tweets_conversation_id ON tweets(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_tweets_in_reply_to ON tweets(in_reply_to_status_id);
            CREATE INDEX IF NOT EXISTS idx_tweets_quoted ON tweets(quoted_tweet_id);
            CREATE INDEX IF NOT EXISTS idx_tweets_retweeted ON tweets(retweeted_tweet_id);
            CREATE INDEX IF NOT EXISTS idx_tweets_origin ON tweets(origin);
            CREATE INDEX IF NOT EXISTS idx_tweets_status ON tweets(status);
            CREATE INDEX IF NOT EXISTS idx_tweets_author ON tweets(author_username);
            CREATE INDEX IF NOT EXISTS idx_tweets_discovered_via ON tweets(discovered_via_tweet_id);
            CREATE INDEX IF NOT EXISTS idx_tweets_thread_root ON tweets(thread_root_id);
            CREATE INDEX IF NOT EXISTS idx_tweets_created_at ON tweets(created_at);
        """)
        await self._connection.commit()

    async def tweet_exists(self, tweet_id: str) -> bool:
        cursor = await self._connection.execute(
            "SELECT 1 FROM tweets WHERE tweet_id = ?", (tweet_id,)
        )
        return await cursor.fetchone() is not None

    async def get_tweet(self, tweet_id: str) -> Optional[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM tweets WHERE tweet_id = ?", (tweet_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_tweet_ids(self) -> Set[str]:
        cursor = await self._connection.execute("SELECT tweet_id FROM tweets")
        rows = await cursor.fetchall()
        return {row["tweet_id"] for row in rows}

    async def get_liked_tweet_ids(self) -> Set[str]:
        cursor = await self._connection.execute(
            "SELECT tweet_id FROM tweets WHERE origin = 'liked'"
        )
        rows = await cursor.fetchall()
        return {row["tweet_id"] for row in rows}

    async def insert_tweet(
        self,
        tweet_id: str,
        author_username: str,
        post_url: str,
        origin: str,
        author_name: Optional[str] = None,
        author_id: Optional[str] = None,
        tweet_text: Optional[str] = None,
        created_at: Optional[datetime] = None,
        conversation_id: Optional[str] = None,
        in_reply_to_status_id: Optional[str] = None,
        quoted_tweet_id: Optional[str] = None,
        retweeted_tweet_id: Optional[str] = None,
        is_retweet: bool = False,
        reply_count: Optional[int] = None,
        retweet_count: Optional[int] = None,
        like_count: Optional[int] = None,
        quote_count: Optional[int] = None,
        bookmark_count: Optional[int] = None,
        view_count: Optional[int] = None,
        has_media: bool = False,
        media_count: int = 0,
        media_types: Optional[List[str]] = None,
        media_urls: Optional[List[str]] = None,
        discovered_via_tweet_id: Optional[str] = None,
        thread_position: Optional[str] = None,
        has_self_replies: Optional[bool] = None,
        thread_root_id: Optional[str] = None,
        status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        media_types_json = json.dumps(media_types) if media_types else None
        media_urls_json = json.dumps(media_urls) if media_urls else None
        metadata_json = json.dumps(metadata) if metadata else None
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO tweets
            (tweet_id, author_username, author_name, author_id, tweet_text, post_url,
             created_at, conversation_id, in_reply_to_status_id, quoted_tweet_id,
             retweeted_tweet_id, is_retweet, reply_count, retweet_count, like_count,
             quote_count, bookmark_count, view_count, has_media, media_count, media_types,
             media_urls, origin, discovered_via_tweet_id, thread_position, has_self_replies,
             thread_root_id, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tweet_id, author_username, author_name, author_id, tweet_text, post_url,
                created_at, conversation_id, in_reply_to_status_id, quoted_tweet_id,
                retweeted_tweet_id, 1 if is_retweet else 0, reply_count, retweet_count,
                like_count, quote_count, bookmark_count, view_count,
                1 if has_media else 0, media_count, media_types_json,
                media_urls_json, origin, discovered_via_tweet_id, thread_position,
                1 if has_self_replies else (0 if has_self_replies is not None else None),
                thread_root_id, status, metadata_json,
            ),
        )
        await self._connection.commit()

    async def bulk_insert_tweets(self, tweets: List[Dict[str, Any]]):
        """Insert multiple tweets in a single transaction."""
        for tweet in tweets:
            media_types = tweet.get("media_types")
            media_urls = tweet.get("media_urls")
            metadata = tweet.get("metadata")
            has_self_replies = tweet.get("has_self_replies")
            await self._connection.execute(
                """
                INSERT OR IGNORE INTO tweets
                (tweet_id, author_username, author_name, author_id, tweet_text, post_url,
                 created_at, conversation_id, in_reply_to_status_id, quoted_tweet_id,
                 retweeted_tweet_id, is_retweet, reply_count, retweet_count, like_count,
                 quote_count, bookmark_count, view_count, has_media, media_count, media_types,
                 media_urls, origin, discovered_via_tweet_id, thread_position, has_self_replies,
                 thread_root_id, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tweet["tweet_id"], tweet["author_username"], tweet.get("author_name"),
                    tweet.get("author_id"), tweet.get("tweet_text"), tweet["post_url"],
                    tweet.get("created_at"), tweet.get("conversation_id"),
                    tweet.get("in_reply_to_status_id"), tweet.get("quoted_tweet_id"),
                    tweet.get("retweeted_tweet_id"), 1 if tweet.get("is_retweet") else 0,
                    tweet.get("reply_count"), tweet.get("retweet_count"),
                    tweet.get("like_count"), tweet.get("quote_count"),
                    tweet.get("bookmark_count"), tweet.get("view_count"),
                    1 if tweet.get("has_media") else 0, tweet.get("media_count", 0),
                    json.dumps(media_types) if media_types else None,
                    json.dumps(media_urls) if media_urls else None,
                    tweet["origin"], tweet.get("discovered_via_tweet_id"),
                    tweet.get("thread_position"),
                    1 if has_self_replies else (0 if has_self_replies is not None else None),
                    tweet.get("thread_root_id"), tweet.get("status", "pending"),
                    json.dumps(metadata) if metadata else None,
                ),
            )
        await self._connection.commit()

    async def update_status(
        self, tweet_id: str, status: str, error_message: Optional[str] = None
    ):
        await self._connection.execute(
            "UPDATE tweets SET status = ?, error_message = ? WHERE tweet_id = ?",
            (status, error_message, tweet_id),
        )
        await self._connection.commit()

    async def mark_downloaded(self, tweet_id: str, local_paths: List[str]):
        await self._connection.execute(
            """
            UPDATE tweets
            SET status = 'downloaded', downloaded_at = ?, local_paths = ?
            WHERE tweet_id = ?
            """,
            (datetime.now(), json.dumps(local_paths), tweet_id),
        )
        await self._connection.commit()

    async def mark_uploaded(self, tweet_id: str, telegram_message_ids: List[int]):
        await self._connection.execute(
            """
            UPDATE tweets
            SET status = 'uploaded', uploaded_at = ?, telegram_message_ids = ?
            WHERE tweet_id = ?
            """,
            (datetime.now(), json.dumps(telegram_message_ids), tweet_id),
        )
        await self._connection.commit()

    async def mark_embedded(
        self, tweet_id: str, success: bool = True, error_message: Optional[str] = None,
        vlm_description: Optional[str] = None
    ):
        status = "completed" if success else "failed"
        await self._connection.execute(
            """
            UPDATE tweets
            SET embedding_status = ?, embedded_at = ?, error_message = ?, vlm_description = ?
            WHERE tweet_id = ?
            """,
            (status, datetime.now() if success else None, error_message, vlm_description, tweet_id),
        )
        await self._connection.commit()

    async def get_pending_tweets(self) -> List[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM tweets WHERE status IN ('pending', 'downloaded') ORDER BY fetched_at"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_tweets_by_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM tweets WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_thread(self, thread_root_id: str) -> List[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM tweets WHERE thread_root_id = ? ORDER BY created_at",
            (thread_root_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_replies_to(self, tweet_id: str) -> List[Dict[str, Any]]:
        cursor = await self._connection.execute(
            "SELECT * FROM tweets WHERE in_reply_to_status_id = ? ORDER BY created_at",
            (tweet_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_stats(self) -> Dict[str, Dict[str, int]]:
        cursor = await self._connection.execute(
            """
            SELECT
                origin,
                COUNT(*) as total,
                SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) as uploaded,
                SUM(CASE WHEN embedding_status = 'completed' THEN 1 ELSE 0 END) as embedded
            FROM tweets
            GROUP BY origin
            """
        )
        rows = await cursor.fetchall()
        return {
            row["origin"]: {
                "total": row["total"],
                "uploaded": row["uploaded"],
                "embedded": row["embedded"],
            }
            for row in rows
        }

    async def get_tweets_without_embeddings(
        self, origin: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT * FROM tweets
            WHERE status = 'uploaded'
            AND (embedding_status IS NULL OR embedding_status = 'failed')
        """
        params = []

        if origin:
            query += " AND origin = ?"
            params.append(origin)

        query += " ORDER BY uploaded_at DESC"

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
