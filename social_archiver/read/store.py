"""Read-only access to the per-platform archives, for the UI, search, CLI and MCP.

Opens each database with `mode=ro` and never migrates: the archival process owns the schema.
A database that predates a column is padded with NULLs at SELECT time, and one that does not
exist yet simply contributes no rows — so a viewer can point at any generation of archive,
including one another machine is still writing.

Ordering is (created_at DESC, item_id DESC) with NULL timestamps last, and cursors carry the
raw stored timestamp string: comparisons happen in SQL against the same strings, so mixed
timestamp formats across platforms cannot skip or repeat rows within one archive.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from social_archiver.core.config import PLATFORMS
from social_archiver.core.database import _ITEM_COLUMNS, Item
from social_archiver.read.models import (
    SEED_ORIGINS,
    AuthorCount,
    ChatSummary,
    Facets,
    ItemFilters,
    Page,
    PlatformStats,
    SearchHit,
    decode_cursor,
    encode_cursor,
)

_SORT = "ORDER BY created_at DESC, item_id DESC"
_FTS_COLUMNS = ("text", "vlm_description", "author_username", "chat_name")


@dataclass(slots=True)
class _Archive:
    platform: str
    connection: aiosqlite.Connection
    columns: frozenset[str]
    tables: frozenset[str]  # auxiliary tables too can predate an old archive
    select: str  # every Item column, absent ones padded as NULL
    has_fts: bool


class ArchiveReader:
    def __init__(self, data_dir: Path, platforms: tuple[str, ...] = PLATFORMS):
        self.data_dir = data_dir
        self.platforms = platforms
        self._archives: dict[str, _Archive] = {}

    async def close(self):
        for archive in self._archives.values():
            await archive.connection.close()
        self._archives.clear()

    async def _archive(self, platform: str) -> _Archive | None:
        """None while the platform's database does not exist; re-checked per call so an archive
        created after startup (a platform's first run) appears without a restart."""
        if platform in self._archives:
            return self._archives[platform]
        path = self.data_dir / f"{platform}.db"
        if not path.exists():
            return None
        connection = await aiosqlite.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute("PRAGMA table_info(items)")
        columns = frozenset(row["name"] for row in await cursor.fetchall())
        if not columns:  # the file exists but the archiver has not created the schema yet
            await connection.close()
            return None
        select = ", ".join(name if name in columns else f"NULL AS {name}" for name in _ITEM_COLUMNS)
        cursor = await connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = frozenset(row["name"] for row in await cursor.fetchall())
        self._archives[platform] = _Archive(platform, connection, columns, tables, select, "items_fts" in tables)
        return self._archives[platform]

    async def present(self) -> list[str]:
        return [p for p in self.platforms if await self._archive(p) is not None]

    def _selected(self, filters: ItemFilters) -> tuple[str, ...]:
        return tuple(p for p in filters.platforms if p in self.platforms) or self.platforms

    @staticmethod
    def _where(filters: ItemFilters, archive: _Archive, p: str = "") -> tuple[list[str], list]:
        """`p` prefixes item columns ("items.") where a join makes bare names ambiguous."""
        columns, tables = archive.columns, archive.tables
        clauses: list[str] = []
        params: list = []

        def add(clause: str, *values):
            clauses.append(clause)
            params.extend(values)

        if filters.category:
            # items.category records only the first sighting; membership lives in item_categories
            if "item_categories" in tables:
                add(
                    f"({p}category = ? OR {p}item_id IN (SELECT item_id FROM item_categories WHERE category = ?))",
                    filters.category,
                    filters.category,
                )
            else:
                add(f"{p}category = ?", filters.category)
        if filters.author:
            add(f"{p}author_username = ?", filters.author)
        if filters.subreddit:
            add(f"{p}subreddit = ?", filters.subreddit)
        if filters.chat:
            add(f"{p}conversation_id = ?", filters.chat)
        if filters.collection:
            if "item_collections" in tables:
                add(f"{p}item_id IN (SELECT item_id FROM item_collections WHERE collection = ?)", filters.collection)
            else:
                add("0")
        if filters.origin:
            add(f"{p}origin = ?", filters.origin)
        if filters.archive_status:
            add(f"{p}archive_status = ?", filters.archive_status)
        if filters.source_target:
            # A database from before the column existed holds nothing a source walk attributed
            if "source_target" in columns:
                add(f"{p}source_target = ?", filters.source_target)
            else:
                add("0")
        if filters.has_media is not None:
            add(f"{p}has_media = ?", int(filters.has_media))
        # Platforms without a seed vocabulary hold only seeds; no clause needed there
        if filters.seeds_only and (seeds := SEED_ORIGINS.get(archive.platform)):
            placeholders = ",".join("?" * len(seeds))
            add(f"({p}origin IS NULL OR {p}origin IN ({placeholders}))", *sorted(seeds))
        if filters.date_from:
            add(f"{p}created_at >= ?", filters.date_from.isoformat())
        if filters.date_to:
            add(f"{p}created_at < ?", filters.date_to.isoformat())
        return clauses, params

    async def _fetch(
        self, platform: str, filters: ItemFilters, cursor: str | None, limit: int
    ) -> list[tuple[str | None, Item]]:
        """Rows as (raw created_at string, Item): the raw string is the merge and cursor key.

        The timestamped run and the NULL tail are separate queries: a single OR spanning both
        turns into a multi-index scan that sorts every row below the boundary, where the split
        keeps each half on the created_at index and streaming under its LIMIT."""
        archive = await self._archive(platform)
        if archive is None:
            return []
        key, item_id = decode_cursor(cursor) if cursor else (False, "")  # False: before the first row

        async def run(extra: list[str], extra_params: list, order: str, n: int) -> list:
            clauses, params = self._where(filters, archive)
            clauses += extra
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return await archive.connection.execute_fetchall(
                f"SELECT {archive.select}, created_at AS _key FROM items {where} {order} LIMIT ?",
                (*params, *extra_params, n),
            )

        rows: list = []
        if key is not None:  # not yet in the NULL tail
            timed = ["created_at IS NOT NULL"]
            timed_params: list = []
            if key:
                # Indexable coarse bound, with the exact boundary as a residual tie filter
                timed = ["created_at <= ?", "(created_at < ? OR item_id < ?)"]
                timed_params = [key, key, item_id]
            rows = list(await run(timed, timed_params, _SORT, limit))
        if len(rows) < limit:
            tail = ["created_at IS NULL"]
            tail_params: list = []
            if key is None:
                tail.append("item_id < ?")
                tail_params.append(item_id)
            rows += await run(tail, tail_params, "ORDER BY item_id DESC", limit - len(rows))
        return [(row["_key"], Item.from_row(row)) for row in rows]

    @staticmethod
    def _merge(batches: list[list[tuple[str | None, Item]]], limit: int) -> Page:
        merged = sorted(
            (entry for batch in batches for entry in batch),
            key=lambda entry: (entry[0] is not None, entry[0] or "", entry[1].item_id),
            reverse=True,
        )
        page = merged[:limit]
        next_cursor = None
        if len(merged) > limit and page:
            key, last = page[-1]
            next_cursor = encode_cursor(None, last.item_id) if key is None else _raw_cursor(key, last.item_id)
        return Page(items=[item for _, item in page], next_cursor=next_cursor)

    async def list_items(self, filters: ItemFilters, cursor: str | None = None, limit: int = 50) -> Page:
        batches = [await self._fetch(p, filters, cursor, limit + 1) for p in self._selected(filters)]
        return self._merge(batches, limit)

    async def count(self, filters: ItemFilters) -> int:
        total = 0
        for platform in self._selected(filters):
            archive = await self._archive(platform)
            if archive is None:
                continue
            clauses, params = self._where(filters, archive)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = await archive.connection.execute_fetchall(f"SELECT count(*) AS n FROM items {where}", params)
            total += rows[0]["n"]
        return total

    # =========================================================================
    # Search
    # =========================================================================

    async def search(self, query: str, filters: ItemFilters, limit: int = 30, offset: int = 0) -> list[SearchHit]:
        """FTS where the archive has been migrated, LIKE elsewhere: a read-only consumer never
        migrates, so both generations must answer."""
        hits: list[tuple[float, SearchHit]] = []
        for platform in self._selected(filters):
            archive = await self._archive(platform)
            if archive is None:
                continue
            if archive.has_fts:
                hits += await self._search_fts(archive, query, filters, limit + offset)
            else:
                hits += await self._search_like(archive, query, filters, limit + offset)
        hits.sort(key=lambda entry: entry[0])
        return [hit for _, hit in hits[offset : offset + limit]]

    async def _search_fts(
        self, archive: _Archive, query: str, filters: ItemFilters, limit: int
    ) -> list[tuple[float, SearchHit]]:
        clauses, params = self._where(filters, archive, p="items.")
        where = f"AND {' AND '.join(clauses)}" if clauses else ""
        qualified = ", ".join(
            f"items.{name}" if name in archive.columns else f"NULL AS {name}" for name in _ITEM_COLUMNS
        )
        sql = f"""
            SELECT {qualified}, rank AS _rank, snippet(items_fts, -1, '[', ']', '…', 18) AS _snippet
            FROM items_fts JOIN items ON items.rowid = items_fts.rowid
            WHERE items_fts MATCH ? {where} ORDER BY rank LIMIT ?
        """
        try:
            rows = await archive.connection.execute_fetchall(sql, (query, *params, limit))
        except sqlite3.OperationalError:
            # Unbalanced quotes or a stray operator: retry as a literal phrase
            literal = '"' + query.replace('"', '""') + '"'
            rows = await archive.connection.execute_fetchall(sql, (literal, *params, limit))
        return [(row["_rank"], SearchHit(Item.from_row(row), row["_snippet"])) for row in rows]

    async def _search_like(
        self, archive: _Archive, query: str, filters: ItemFilters, limit: int
    ) -> list[tuple[float, SearchHit]]:
        clauses, params = self._where(filters, archive)
        pattern = "%" + re.sub(r"([%_\\])", r"\\\1", query) + "%"
        columns = [c for c in _FTS_COLUMNS if c in archive.columns]
        clauses.insert(0, "(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in columns) + ")")
        params = [pattern] * len(columns) + params
        rows = await archive.connection.execute_fetchall(
            f"SELECT {archive.select} FROM items WHERE {' AND '.join(clauses)} {_SORT} LIMIT ?",
            (*params, limit),
        )
        # No rank exists; recency stands in so merged results stay deterministic
        return [(float(index), SearchHit(Item.from_row(row), None)) for index, row in enumerate(rows)]

    # =========================================================================
    # Single items and their surroundings
    # =========================================================================

    async def get(self, platform: str, item_id: str) -> Item | None:
        archive = await self._archive(platform)
        if archive is None:
            return None
        rows = await archive.connection.execute_fetchall(
            f"SELECT {archive.select} FROM items WHERE item_id = ?", (item_id,)
        )
        return Item.from_row(rows[0]) if rows else None

    async def get_many(self, platform: str, item_ids: set[str]) -> dict[str, Item]:
        """Batch lookup for context resolution: one page of items references at most a few
        dozen parents/quotes, so a single IN query replaces N gets."""
        archive = await self._archive(platform)
        if archive is None or not item_ids:
            return {}
        ids = list(item_ids)
        found: dict[str, Item] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = await archive.connection.execute_fetchall(
                f"SELECT {archive.select} FROM items WHERE item_id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
            found.update({row["item_id"]: Item.from_row(row) for row in rows})
        return found

    async def related(self, platform: str, item: Item) -> dict[str, Item | None]:
        """The graph neighbours an item points at; keys with nothing to show are None."""
        ids = {
            "parent": item.in_reply_to_status_id,
            "quoted": item.quoted_tweet_id,
            "retweeted": item.retweeted_tweet_id,
            "discovered_via": item.discovered_via_item_id,
        }
        return {name: await self.get(platform, target) if target else None for name, target in ids.items()}

    async def replies(self, platform: str, item_id: str, limit: int = 100) -> list[Item]:
        archive = await self._archive(platform)
        if archive is None:
            return []
        rows = await archive.connection.execute_fetchall(
            f"SELECT {archive.select} FROM items WHERE in_reply_to_status_id = ? "
            f"ORDER BY created_at ASC, item_id ASC LIMIT ?",
            (item_id, limit),
        )
        return [Item.from_row(row) for row in rows]

    async def memberships(self, platform: str, item_id: str) -> tuple[list[str], list[str]]:
        """(categories, collections) an item belongs to beyond its first-sighting column."""
        archive = await self._archive(platform)
        if archive is None:
            return [], []
        categories, collections = [], []
        if "item_categories" in archive.tables:
            rows = await archive.connection.execute_fetchall(
                "SELECT category FROM item_categories WHERE item_id = ? ORDER BY category", (item_id,)
            )
            categories = [row["category"] for row in rows]
        if "item_collections" in archive.tables:
            rows = await archive.connection.execute_fetchall(
                "SELECT collection FROM item_collections WHERE item_id = ? ORDER BY collection", (item_id,)
            )
            collections = [row["collection"] for row in rows]
        return categories, collections

    async def conversation(self, platform: str, conversation_id: str, limit: int = 500) -> list[Item]:
        """Every archived member of one discussion, chronological. Bounded: a conversation is
        a tree of at most a few hundred archived items, never a whole chat."""
        archive = await self._archive(platform)
        if archive is None:
            return []
        rows = await archive.connection.execute_fetchall(
            f"SELECT {archive.select} FROM items WHERE conversation_id = ? "
            f"ORDER BY created_at ASC, item_id ASC LIMIT ?",
            (conversation_id, limit),
        )
        return [Item.from_row(row) for row in rows]

    async def thread(self, platform: str, root_id: str, limit: int = 500) -> list[Item]:
        """Chronological, root included even where it predates thread_root_id stamping."""
        archive = await self._archive(platform)
        if archive is None:
            return []
        rows = await archive.connection.execute_fetchall(
            f"SELECT {archive.select} FROM items WHERE thread_root_id = ? OR item_id = ? "
            f"ORDER BY created_at ASC, item_id ASC LIMIT ?",
            (root_id, root_id, limit),
        )
        return [Item.from_row(row) for row in rows]

    # =========================================================================
    # Aggregates
    # =========================================================================

    async def chats(self, platform: str) -> list[ChatSummary]:
        """One row per conversation, newest first, with the latest message as its preview."""
        archive = await self._archive(platform)
        if archive is None:
            return []
        chat_name = "chat_name" if "chat_name" in archive.columns else "NULL"
        rows = await archive.connection.execute_fetchall(
            f"""
            SELECT * FROM (
                SELECT conversation_id, {chat_name} AS chat_name, category, text, created_at, author_username,
                       count(*) OVER (PARTITION BY conversation_id) AS n,
                       row_number() OVER (PARTITION BY conversation_id ORDER BY created_at DESC) AS rn
                FROM items WHERE conversation_id IS NOT NULL
            ) WHERE rn = 1 ORDER BY created_at DESC
            """
        )
        return [
            ChatSummary(
                chat_id=row["conversation_id"],
                name=row["chat_name"],
                category=row["category"],
                message_count=row["n"],
                last_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                last_author=row["author_username"],
                last_text=row["text"],
            )
            for row in rows
        ]

    async def authors(self, platform: str, prefix: str = "", limit: int = 20) -> list[AuthorCount]:
        archive = await self._archive(platform)
        if archive is None:
            return []
        pattern = re.sub(r"([%_\\])", r"\\\1", prefix) + "%"
        rows = await archive.connection.execute_fetchall(
            "SELECT author_username, count(*) AS n FROM items WHERE author_username LIKE ? ESCAPE '\\' "
            "GROUP BY author_username ORDER BY n DESC LIMIT ?",
            (pattern, limit),
        )
        return [AuthorCount(row["author_username"], row["n"]) for row in rows]

    async def facets(self, platform: str) -> Facets:
        archive = await self._archive(platform)
        if archive is None:
            return Facets()

        async def grouped(sql: str) -> dict[str, int]:
            rows = await archive.connection.execute_fetchall(sql)
            return {row[0]: row[1] for row in rows if row[0]}

        return Facets(
            categories=await grouped("SELECT category, count(*) FROM items GROUP BY 1 ORDER BY 2 DESC"),
            origins=await grouped("SELECT origin, count(*) FROM items GROUP BY 1 ORDER BY 2 DESC"),
            subreddits=await grouped("SELECT subreddit, count(*) FROM items GROUP BY 1 ORDER BY 2 DESC LIMIT 50"),
            collections=await grouped("SELECT collection, count(*) FROM item_collections GROUP BY 1 ORDER BY 2 DESC")
            if "item_collections" in archive.tables
            else {},
        )

    async def stats(self, platform: str) -> PlatformStats:
        archive = await self._archive(platform)
        stats = PlatformStats(platform=platform)
        if archive is None:
            return stats

        async def one(sql: str) -> aiosqlite.Row:
            return (await archive.connection.execute_fetchall(sql))[0]

        async def grouped(sql: str) -> dict[str, int]:
            return {row[0]: row[1] for row in await archive.connection.execute_fetchall(sql)}

        totals = await one(
            "SELECT count(*) AS total, sum(has_media) AS media, "
            "sum(local_paths IS NOT NULL) AS held, count(DISTINCT author_username) AS authors, "
            "min(created_at) AS oldest, max(created_at) AS newest FROM items"
        )
        stats.total = totals["total"]
        stats.with_media = totals["media"] or 0
        stats.with_local_media = totals["held"] or 0
        stats.authors = totals["authors"]
        stats.oldest = datetime.fromisoformat(totals["oldest"]) if totals["oldest"] else None
        stats.newest = datetime.fromisoformat(totals["newest"]) if totals["newest"] else None
        stats.categories = await grouped("SELECT category, count(*) FROM items GROUP BY 1")
        stats.archive = await grouped("SELECT archive_status, count(*) FROM items GROUP BY 1")
        stats.upload = await grouped("SELECT upload_status, count(*) FROM items GROUP BY 1")
        stats.embed = await grouped("SELECT embed_status, count(*) FROM items GROUP BY 1")
        stats.by_month = await grouped(
            "SELECT strftime('%Y-%m', created_at), count(*) FROM items "
            "WHERE created_at IS NOT NULL GROUP BY 1 ORDER BY 1"
        )
        return stats


def _raw_cursor(key: str, item_id: str) -> str:
    """encode_cursor takes a datetime; the raw stored string must pass through untouched, so
    boundary comparisons in SQL happen against the exact text the database ordered by."""
    import base64
    import json

    payload = json.dumps({"k": key, "id": item_id})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
