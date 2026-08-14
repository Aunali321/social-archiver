import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

import aiosqlite

from social_archiver.core import migrations
from social_archiver.llm.vlm_types import VlmTrace

MIGRATIONS = (
    (
        """
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
                    )
        """,
        "CREATE INDEX IF NOT EXISTS idx_items_platform_category ON items(platform, category)",
        "CREATE INDEX IF NOT EXISTS idx_items_archive_status ON items(platform, archive_status)",
        "CREATE INDEX IF NOT EXISTS idx_items_upload_status ON items(platform, upload_status)",
        "CREATE INDEX IF NOT EXISTS idx_items_embed_status ON items(platform, embed_status)",
        "CREATE INDEX IF NOT EXISTS idx_items_conversation_id ON items(conversation_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_origin ON items(origin)",
        "CREATE INDEX IF NOT EXISTS idx_items_author ON items(author_username)",
        "CREATE INDEX IF NOT EXISTS idx_items_thread_root ON items(thread_root_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at)",
        """
        -- An item can belong to several categories at once (a post you wrote is also
                    -- one you upvoted), which items.category alone cannot express: it only records
                    -- whichever pass inserted the row first.
                    CREATE TABLE IF NOT EXISTS item_categories (
                        item_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        PRIMARY KEY (item_id, category)
                    )
        """,
        "CREATE INDEX IF NOT EXISTS idx_item_categories_category ON item_categories(category)",
        """
        -- A saved post can sit in several Instagram collections at once, and can be moved
                    -- between them later. items.collection_name records only where it was first seen.
                    CREATE TABLE IF NOT EXISTS item_collections (
                        item_id TEXT NOT NULL,
                        collection TEXT NOT NULL,
                        PRIMARY KEY (item_id, collection)
                    )
        """,
        "CREATE INDEX IF NOT EXISTS idx_item_collections_collection ON item_collections(collection)",
        """
        -- Where a history walk had got to, so an interrupted one resumes instead of
                    -- paging from the top again. Only a full backfill writes here.
                    CREATE TABLE IF NOT EXISTS fetch_cursors (
                        platform TEXT NOT NULL,
                        category TEXT NOT NULL,
                        cursor TEXT NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        PRIMARY KEY (platform, category)
                    )
        """,
        """
        -- Conversations thread discovery has already walked. Pairs are rederived from the
                    -- whole archive every run, so without this a repeat run researches what it has.
                    CREATE TABLE IF NOT EXISTS searched_conversations (
                        platform TEXT NOT NULL,
                        author TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        searched_at TIMESTAMP NOT NULL,
                        PRIMARY KEY (platform, author, conversation_id)
                    )
        """,
    ),
    # Which source pulled a row in. Null for everything the category walks archive, so the
    # two populations stay distinguishable without reading the category back.
    (
        "ALTER TABLE items ADD COLUMN source_target TEXT",
        "CREATE INDEX IF NOT EXISTS idx_items_source_target ON items(platform, source_target)",
    ),
    # The chat or group a message sits in, for platforms shaped as conversations rather than
    # posts. A group's name is neither a subreddit nor a collection, so it gets its own column.
    ("ALTER TABLE items ADD COLUMN chat_name TEXT",),
    # Substring search over hundreds of thousands of rows is a full table scan, so the archive
    # is write-only in practice. An external-content FTS index makes it browsable; triggers
    # keep it in step with every write path, and the rebuild backfills the rows already held.
    (
        """
        CREATE VIRTUAL TABLE items_fts USING fts5(
                        text, vlm_description, author_username, chat_name,
                        content='items', content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    )
        """,
        """
        CREATE TRIGGER items_fts_after_insert AFTER INSERT ON items BEGIN
                        INSERT INTO items_fts(rowid, text, vlm_description, author_username, chat_name)
                        VALUES (new.rowid, new.text, new.vlm_description, new.author_username, new.chat_name);
                    END
        """,
        """
        CREATE TRIGGER items_fts_after_delete AFTER DELETE ON items BEGIN
                        INSERT INTO items_fts(items_fts, rowid, text, vlm_description, author_username, chat_name)
                        VALUES ('delete', old.rowid, old.text, old.vlm_description, old.author_username, old.chat_name);
                    END
        """,
        """
        CREATE TRIGGER items_fts_after_update AFTER UPDATE OF text, vlm_description, author_username, chat_name
                    ON items BEGIN
                        INSERT INTO items_fts(items_fts, rowid, text, vlm_description, author_username, chat_name)
                        VALUES ('delete', old.rowid, old.text, old.vlm_description, old.author_username, old.chat_name);
                        INSERT INTO items_fts(rowid, text, vlm_description, author_username, chat_name)
                        VALUES (new.rowid, new.text, new.vlm_description, new.author_username, new.chat_name);
                    END
        """,
        "INSERT INTO items_fts(items_fts) VALUES ('rebuild')",
    ),
    # Every VLM captioning call, recorded whole: the interleaved input (media as
    # path references, never bytes), the model's reasoning and raw output, usage
    # and a status. Cost accounting reads it, a refused-item retry reads it, and
    # it is the distillation dataset a later fine-tune trains on.
    (
        """
        CREATE TABLE IF NOT EXISTS vlm_traces (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        model TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        status TEXT NOT NULL,
                        finish_reason TEXT,
                        params TEXT NOT NULL,
                        target_item_ids TEXT NOT NULL,
                        input TEXT NOT NULL,
                        reasoning TEXT,
                        output TEXT,
                        usage TEXT,
                        error TEXT
                    )
        """,
        "CREATE INDEX IF NOT EXISTS idx_vlm_traces_platform_status ON vlm_traces(platform, status)",
    ),
    # Captioning is its own stage, independent of building the search index: the
    # VLM captions a media item (caption_status), and only later is that caption
    # embedded into the vector index (embed_status). Split, captioning needs no
    # embedding server, and each stage retries on its own. Items already done
    # under the old combined stage are captioned and indexed, so backfill both.
    (
        "ALTER TABLE items ADD COLUMN caption_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE items ADD COLUMN caption_error TEXT",
        "ALTER TABLE items ADD COLUMN captioned_at TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS idx_items_caption_status ON items(platform, caption_status)",
        "UPDATE items SET caption_status = 'done', captioned_at = embedded_at "
        "WHERE embed_status = 'done' AND vlm_description IS NOT NULL",
    ),
)


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
    # Embed only: the VLM declined this content (safety/policy/recitation), as
    # opposed to a technical failure. Kept apart so a scheduled retry can target
    # refusals without also re-running everything that failed transiently.
    REFUSED = "refused"


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
    link_url: str | None = None

    # WhatsApp-specific: the chat or group the message belongs to
    chat_name: str | None = None

    # Set when a source walk archived the row, naming which account or subreddit
    source_target: str | None = None  # a link post's outbound target, which post_url is not

    archive_status: ArchiveStatus = ArchiveStatus.PENDING
    upload_status: StageStatus = StageStatus.PENDING
    caption_status: StageStatus = StageStatus.PENDING
    embed_status: StageStatus = StageStatus.PENDING
    archive_error: str | None = None
    upload_error: str | None = None
    caption_error: str | None = None
    embed_error: str | None = None
    archive_attempts: int = 0

    captioned_at: datetime | None = None
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
            chat_name=row["chat_name"],
            source_target=row["source_target"],
            archive_status=ArchiveStatus(row["archive_status"]),
            upload_status=StageStatus(row["upload_status"]),
            caption_status=StageStatus(row["caption_status"]),
            embed_status=StageStatus(row["embed_status"]),
            archive_error=row["archive_error"],
            upload_error=row["upload_error"],
            caption_error=row["caption_error"],
            embed_error=row["embed_error"],
            archive_attempts=row["archive_attempts"],
            captioned_at=datetime.fromisoformat(row["captioned_at"]) if row["captioned_at"] else None,
            local_paths=[Path(p) for p in json.loads(row["local_paths"] or "[]")],
            telegram_message_ids=json.loads(row["telegram_message_ids"] or "[]"),
            vlm_description=row["vlm_description"],
        )


@dataclass(frozen=True, slots=True)
class HeldItem:
    """The little of an archived row a batch lookup needs: enough to tell whether a source has
    something better than what is already stored, without reading the row itself."""

    item_id: str
    text: str | None
    archive_status: ArchiveStatus

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "HeldItem":
        return cls(row["item_id"], row["text"], ArchiveStatus(row["archive_status"]))


_ITEM_COLUMNS = [f.name for f in fields(Item)]

# sqlite caps the host parameters one statement may carry. A lookup wider than this is split
# across several statements rather than silently returning a short answer.
_MAX_PARAMETERS = 900


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
        await migrations.apply(self._connection, MIGRATIONS, "archive", self.db_path)

    async def close(self):
        if self._connection:
            await self._connection.close()

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

    async def insert_many(self, items: Sequence[Item]):
        """The whole batch in one transaction. `insert` commits per row, which is what an
        interactive fetch wants and what a walk of a source cannot afford: a subreddit runs to
        millions of items, and a commit each turns the ingest into that many round trips."""
        if not items:
            return
        columns = ", ".join(_ITEM_COLUMNS)
        placeholders = ", ".join("?" * len(_ITEM_COLUMNS))
        await self._connection.executemany(
            f"INSERT OR IGNORE INTO items ({columns}) VALUES ({placeholders})",
            [[_to_column(item, name) for name in _ITEM_COLUMNS] for item in items],
        )
        await self._connection.executemany(
            "INSERT OR IGNORE INTO item_categories (item_id, category) VALUES (?, ?)",
            [(item.item_id, item.category) for item in items],
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
        cursor = await self._connection.execute("SELECT collection FROM item_collections WHERE item_id = ?", (item_id,))
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

    async def held(self, item_ids: Sequence[str]) -> dict[str, HeldItem]:
        """What is already archived out of these, by primary key rather than by reading the
        table. `all_ids` answers the same question for a whole run at once, which is right when
        the caller asks once and wrong when it asks per batch: the scan then costs more with
        every batch, against an archive the walk is itself growing.

        No platform predicate, deliberately. Each platform owns its database file, so the
        column is constant within one and narrows nothing, but naming it lets sqlite prefer an
        index that leads with it and walk every row of the platform instead of seeking the
        primary key. Measured at four million rows: 1,764 ms against 0.3 ms."""
        held: dict[str, HeldItem] = {}
        for start in range(0, len(item_ids), _MAX_PARAMETERS):
            chunk = item_ids[start : start + _MAX_PARAMETERS]
            cursor = await self._connection.execute(
                f"SELECT item_id, text, archive_status FROM items WHERE item_id IN ({', '.join('?' * len(chunk))})",
                chunk,
            )
            held.update({row["item_id"]: HeldItem.from_row(row) for row in await cursor.fetchall()})
        return held

    async def restore_content(self, items: Sequence[Item]) -> int:
        """Put back what the platform has dropped, from a source that still has it.

        The caller decides which rows qualify, because what counts as lost differs by platform:
        one records a status, another leaves a marker where the text was. What is guaranteed
        here is that a restore never costs anything, hence the columns it will not touch.

        `local_paths` and `upload_status` stay as they are. Files already on disk are not
        re-fetched, and a row already sent to Telegram is not sent twice; a row that skipped
        upload as a tombstone gets its turn. `embed_status` does reset, because an embedding of
        a removal notice describes nothing."""
        if not items:
            return 0
        cursor = await self._connection.executemany(
            """
            UPDATE items
            SET text = ?, author_username = ?, created_at = COALESCE(created_at, ?),
                media_urls = ?, media_types = ?, media_count = ?, has_media = ?,
                link_url = COALESCE(link_url, ?),
                archive_status = CASE
                    WHEN ? AND local_paths IS NULL THEN 'pending'
                    WHEN archive_status = 'tombstone' THEN 'archived'
                    ELSE archive_status END,
                upload_status = CASE WHEN upload_status = 'skipped' THEN 'pending' ELSE upload_status END,
                embed_status = 'pending',
                archive_error = NULL, archive_attempts = 0
            WHERE item_id = ? AND text IS NOT ?
            """,
            [
                (
                    item.text,
                    item.author_username,
                    _to_column(item, "created_at"),
                    _to_column(item, "media_urls"),
                    _to_column(item, "media_types"),
                    item.media_count,
                    int(item.has_media),
                    item.link_url,
                    int(item.has_media),
                    item.item_id,
                    item.text,
                )
                for item in items
            ],
        )
        await self._connection.commit()
        return cursor.rowcount

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

    async def _pending_stage(
        self, stage: str, platform: str, category: str, include_failed: bool, include_refused: bool = False
    ) -> list[Item]:
        statuses = ["pending"]
        if include_failed:
            statuses.append("failed")
        if include_refused:
            statuses.append("refused")
        placeholders = ", ".join("?" * len(statuses))
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND category = ? AND archive_status = 'archived' AND {stage} IN ({placeholders})
            ORDER BY created_at
            """,
            (platform, category, *statuses),
        )

    async def pending_upload(self, platform: str, category: str, include_failed: bool = False) -> list[Item]:
        return await self._pending_stage("upload_status", platform, category, include_failed)

    @staticmethod
    def _caption_statuses(include_failed: bool, include_refused: bool) -> list[str]:
        statuses = ["pending"]
        if include_failed:
            statuses.append("failed")
        if include_refused:
            statuses.append("refused")
        return statuses

    async def pending_caption_roots(
        self, platform: str, category: str, include_failed: bool = False, include_refused: bool = False
    ) -> list[str]:
        """The conversations still holding uncaptioned items, in the order they
        should be captioned: video and animated GIFs first — only Gemini captions
        them well, so they are the scarce capacity — then images, which cheaper
        models can also handle; each newest first (NULLs, the old undated
        backlog, sort last).

        Roots rather than items, so a caller works through a large backlog whole
        conversations at a time instead of holding every pending item and every
        thread member of a platform in memory at once. Just the roots, so this
        stays cheap on a backlog of millions; the items come back per batch."""
        statuses = self._caption_statuses(include_failed, include_refused)
        cursor = await self._connection.execute(
            f"""
            SELECT COALESCE(thread_root_id, item_id) AS root FROM items
            WHERE platform = ? AND category = ? AND archive_status = 'archived'
            AND caption_status IN ({", ".join("?" * len(statuses))})
            GROUP BY root
            ORDER BY
                MIN(CASE WHEN media_types LIKE '%video%' OR media_types LIKE '%animated_gif%' THEN 0 ELSE 1 END),
                MAX(created_at) DESC
            """,
            (platform, category, *statuses),
        )
        return [row["root"] for row in await cursor.fetchall()]

    async def pending_caption(
        self,
        platform: str,
        category: str,
        roots: Sequence[str],
        include_failed: bool = False,
        include_refused: bool = False,
    ) -> list[Item]:
        """The uncaptioned items inside the given conversations, in the same
        video-first order. Only the pending set, so an interrupted run resumes on
        exactly what is left."""
        statuses = self._caption_statuses(include_failed, include_refused)
        items: list[Item] = []
        for start in range(0, len(roots), _MAX_PARAMETERS):
            chunk = roots[start : start + _MAX_PARAMETERS]
            items.extend(
                await self._select_items(
                    f"""
                    SELECT * FROM items
                    WHERE platform = ? AND category = ? AND archive_status = 'archived'
                    AND caption_status IN ({", ".join("?" * len(statuses))})
                    AND COALESCE(thread_root_id, item_id) IN ({", ".join("?" * len(chunk))})
                    ORDER BY
                        CASE WHEN media_types LIKE '%video%' OR media_types LIKE '%animated_gif%' THEN 0 ELSE 1 END,
                        created_at DESC
                    """,
                    (platform, category, *statuses, *chunk),
                )
            )
        return items

    async def pending_embed(self, platform: str, category: str, include_failed: bool = False) -> list[Item]:
        """Items captioned but not yet in the vector index. Indexing follows
        captioning, so an item is never embedded before it has a caption."""
        statuses = ["pending", "failed"] if include_failed else ["pending"]
        placeholders = ", ".join("?" * len(statuses))
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND category = ? AND archive_status = 'archived'
            AND caption_status = 'done' AND embed_status IN ({placeholders})
            ORDER BY created_at
            """,
            (platform, category, *statuses),
        )

    async def items_by_ids(self, platform: str, item_ids: set[str]) -> list[Item]:
        """Archived items by primary key, for context that lives outside the
        thread being embedded — a quoted post in another conversation, which
        `thread_members` never reaches."""
        if not item_ids:
            return []
        ids = list(item_ids)
        items: list[Item] = []
        for start in range(0, len(ids), _MAX_PARAMETERS):
            chunk = ids[start : start + _MAX_PARAMETERS]
            items.extend(
                await self._select_items(
                    f"""
                    SELECT * FROM items
                    WHERE platform = ? AND archive_status = 'archived'
                    AND item_id IN ({", ".join("?" * len(chunk))})
                    """,
                    (platform, *chunk),
                )
            )
        return items

    async def thread_members(self, platform: str, root_ids: set[str]) -> list[Item]:
        """All archived items belonging to the given thread roots (the roots
        themselves included), for VLM context reconstruction."""
        roots = list(root_ids)
        items: list[Item] = []
        # Each chunk binds its roots twice, so it gets half the parameter budget.
        # Members of one thread always share a chunk, which is what keeps them
        # ordered relative to each other once the caller groups by root.
        for start in range(0, len(roots), _MAX_PARAMETERS // 2):
            chunk = roots[start : start + _MAX_PARAMETERS // 2]
            placeholders = ", ".join("?" * len(chunk))
            items.extend(
                await self._select_items(
                    f"""
                    SELECT * FROM items
                    WHERE platform = ? AND archive_status = 'archived'
                    AND (thread_root_id IN ({placeholders}) OR item_id IN ({placeholders}))
                    ORDER BY created_at
                    """,
                    (platform, *chunk, *chunk),
                )
            )
        return items

    async def cleanup_candidates(self, platform: str, require_caption: bool, require_embed: bool) -> list[Item]:
        """Items whose downloads every enabled consumer has finished with.
        Captioning reads the media, so its downloads are kept until it is done
        too — otherwise a caption-later run would find the media gone."""
        done = "IN ('done', 'skipped')"
        caption_filter = f"AND caption_status {done}" if require_caption else ""
        embed_filter = f"AND embed_status {done}" if require_embed else ""
        return await self._select_items(
            f"""
            SELECT * FROM items
            WHERE platform = ? AND local_paths IS NOT NULL
            AND upload_status {done} {caption_filter} {embed_filter}
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

    async def mark_captioned(self, item_id: str, vlm_description: str | None = None):
        await self._connection.execute(
            """
            UPDATE items SET caption_status = 'done', captioned_at = ?, vlm_description = ?, caption_error = NULL
            WHERE item_id = ?
            """,
            (datetime.now().isoformat(), vlm_description, item_id),
        )
        await self._connection.commit()

    async def mark_caption_failed(self, item_id: str, error: str):
        await self._connection.execute(
            "UPDATE items SET caption_status = 'failed', caption_error = ? WHERE item_id = ?",
            (error, item_id),
        )
        await self._connection.commit()

    async def mark_caption_refused(self, item_id: str, reason: str):
        """The VLM declined this content. Distinct from failed so a later pass
        can retry refusals on their own."""
        await self._connection.execute(
            "UPDATE items SET caption_status = 'refused', caption_error = ? WHERE item_id = ?",
            (reason, item_id),
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

    async def insert_trace(self, trace: VlmTrace):
        """Record one captioning call. Committed on its own so a trace survives
        even if the item's status write later fails."""
        await self._connection.execute(
            """
            INSERT INTO vlm_traces
                (platform, model, provider, status, finish_reason, params,
                 target_item_ids, input, reasoning, output, usage, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.platform,
                trace.model,
                trace.provider,
                str(trace.status),
                trace.finish_reason,
                json.dumps(trace.params),
                json.dumps(trace.target_item_ids),
                json.dumps(trace.input),
                trace.reasoning,
                trace.output,
                json.dumps(trace.usage),
                trace.error,
            ),
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

    async def newest_source_item(self, platform: str, target: str) -> str | None:
        """The newest item held for a source, which is where its next walk stops.

        Ids are ordered by time on every platform this archives, so the maximum is the newest.
        Comparing as integers matters: as text, '99' sorts above '100'."""
        cursor = await self._connection.execute(
            """
            SELECT item_id FROM items
            WHERE platform = ? AND source_target = ?
            ORDER BY CAST(item_id AS INTEGER) DESC LIMIT 1
            """,
            (platform, target),
        )
        row = await cursor.fetchone()
        return row["item_id"] if row else None

    async def source_item_count(self, platform: str, target: str) -> int:
        cursor = await self._connection.execute(
            "SELECT count(*) AS n FROM items WHERE platform = ? AND source_target = ?", (platform, target)
        )
        return (await cursor.fetchone())["n"]

    async def attribute_to_source(self, platform: str, item_ids: Iterable[str], target: str):
        """Name the source for rows that are already archived.

        A tweet liked before its author's profile was walked is already stored under `likes`,
        and the walk skips it. Without this it would never be attributed to the source at all:
        the count would understate, and the source could not be listed back. Only fills an
        empty one, so the first source to claim a row keeps it."""
        await self._connection.executemany(
            "UPDATE items SET source_target = ? WHERE item_id = ? AND platform = ? AND source_target IS NULL",
            [(target, item_id, platform) for item_id in item_ids],
        )
        await self._connection.commit()
