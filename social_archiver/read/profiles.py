"""Reading the accounts behind items: an item's author as it shows today, an account's whole
history, and finding accounts by anything they have gone by.

An account's current version is the one seen most recently, which is not always the newest
row: an account that renames and renames back gets a new row for the old name, and that row
is the one last seen.
"""

import re
from datetime import datetime
from pathlib import Path

import aiosqlite

from social_archiver.core.database import Item
from social_archiver.core.profiles import ProfileStatus
from social_archiver.read.models import AuthorRef, Encounters, Profile, ProfileHit, ProfileVersion
from social_archiver.read.store import ArchiveReader

# A picture is shown only once it is in the archive; the key a version names may still be on
# its way, or its link may have died before it was fetched.
_VERSION_SELECT = """
    SELECT v.*, avatar.rowid AS avatar_image, banner.rowid AS banner_image
    FROM profile_versions v
    LEFT JOIN profile_images avatar
        ON avatar.profile_id = v.profile_id AND avatar.key = v.avatar_key AND avatar.path IS NOT NULL
    LEFT JOIN profile_images banner
        ON banner.profile_id = v.profile_id AND banner.key = v.banner_key AND banner.path IS NOT NULL
"""

# Authors by (platform, profile_id), as a page of items resolves them
Authors = dict[tuple[str, str], AuthorRef]

_CHUNK = 500
_SEARCH_CANDIDATES = 200


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _flag(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _ref(platform: str, row: aiosqlite.Row) -> AuthorRef:
    return AuthorRef(
        platform=platform,
        profile_id=row["profile_id"],
        handle=row["handle"],
        display_name=row["display_name"],
        verified=bool(row["verified"]),
        is_self=bool(row["is_self"]),
        avatar=row["avatar_image"],
    )


def _version(row: aiosqlite.Row) -> ProfileVersion:
    return ProfileVersion(
        handle=row["handle"],
        display_name=row["display_name"],
        bio=row["bio"],
        location=row["location"],
        website=row["website"],
        verified=_flag(row["verified"]),
        private=_flag(row["private"]),
        avatar=row["avatar_image"],
        banner=row["banner_image"],
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
    )


def _prefix_expression(query: str) -> str | None:
    """Every word must start some handle, name or bio word: typing narrows, as autocomplete
    should. Quoted so FTS syntax in a name is read as text."""
    words = re.findall(r"[\w@.]+", query)
    return " ".join(f'"{word}"*' for word in words) or None


class ProfileReader:
    def __init__(self, archives: ArchiveReader):
        self.archives = archives

    async def _current(self, connection: aiosqlite.Connection, profile_ids: list[str]) -> list[aiosqlite.Row]:
        rows: list[aiosqlite.Row] = []
        for start in range(0, len(profile_ids), _CHUNK):
            chunk = profile_ids[start : start + _CHUNK]
            rows += await connection.execute_fetchall(
                f"""
                SELECT ranked.*, profiles.is_self FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY profile_id ORDER BY last_seen DESC, version_id DESC) AS _rank
                    FROM ({_VERSION_SELECT} WHERE v.profile_id IN ({",".join("?" * len(chunk))}))
                ) AS ranked JOIN profiles ON profiles.profile_id = ranked.profile_id
                WHERE _rank = 1
                """,
                chunk,
            )
        return rows

    async def refs(self, platform: str, profile_ids: set[str]) -> dict[str, AuthorRef]:
        connection = await self.archives.connection(platform)
        if connection is None or not profile_ids:
            return {}
        return {row["profile_id"]: _ref(platform, row) for row in await self._current(connection, list(profile_ids))}

    async def authors(self, items: list[Item]) -> Authors:
        """The current profile of every author among `items`, one lookup per platform."""
        wanted: dict[str, set[str]] = {}
        for item in items:
            if item.author_id:
                wanted.setdefault(item.platform, set()).add(item.author_id)
        return {
            (platform, profile_id): ref
            for platform, profile_ids in wanted.items()
            for profile_id, ref in (await self.refs(platform, profile_ids)).items()
        }

    async def get(self, platform: str, profile_id: str) -> Profile | None:
        connection = await self.archives.connection(platform)
        if connection is None:
            return None
        rows = await connection.execute_fetchall("SELECT * FROM profiles WHERE profile_id = ?", (profile_id,))
        if not rows:
            return None
        profile = rows[0]
        versions = await connection.execute_fetchall(
            f"{_VERSION_SELECT} WHERE v.profile_id = ? ORDER BY v.last_seen DESC, v.version_id DESC", (profile_id,)
        )
        return Profile(
            platform=platform,
            profile_id=profile_id,
            status=ProfileStatus(profile["status"]),
            is_self=bool(profile["is_self"]),
            followers=profile["followers"],
            following=profile["following"],
            posts=profile["posts"],
            joined_at=_when(profile["joined_at"]),
            first_seen=datetime.fromisoformat(profile["first_seen"]),
            last_seen=datetime.fromisoformat(profile["last_seen"]),
            observed_at=_when(profile["observed_at"]),
            versions=[_version(row) for row in versions],
            encounters=await self._encounters(connection, profile_id),
        )

    async def _encounters(self, connection: aiosqlite.Connection, profile_id: str) -> Encounters:
        rows = await connection.execute_fetchall(
            """
            SELECT origin, count(*) AS n,
                   min(created_at) AS first, max(created_at) AS last
            FROM items WHERE author_id = ? GROUP BY 1 ORDER BY n DESC
            """,
            (profile_id,),
        )
        firsts = [row["first"] for row in rows if row["first"]]
        lasts = [row["last"] for row in rows if row["last"]]
        return Encounters(
            total=sum(row["n"] for row in rows),
            origins={row["origin"]: row["n"] for row in rows},
            first=_when(min(firsts)) if firsts else None,
            last=_when(max(lasts)) if lasts else None,
        )

    async def search(self, query: str, platforms: tuple[str, ...], limit: int = 20) -> list[ProfileHit]:
        """Accounts matching every word of `query` in any handle, name or bio they have had, most
        archived first. An empty query lists the most archived accounts."""
        expression = _prefix_expression(query)
        hits: list[ProfileHit] = []
        for platform in platforms:
            connection = await self.archives.connection(platform)
            if connection is None:
                continue
            if expression:
                rows = await connection.execute_fetchall(
                    """
                    SELECT DISTINCT v.profile_id FROM profiles_fts
                    JOIN profile_versions v ON v.version_id = profiles_fts.rowid
                    WHERE profiles_fts MATCH ? ORDER BY bm25(profiles_fts) LIMIT ?
                    """,
                    (expression, _SEARCH_CANDIDATES),
                )
                candidates = [row["profile_id"] for row in rows]
                counts = await self._item_counts(connection, candidates)
            else:
                rows = await connection.execute_fetchall(
                    "SELECT author_id, count(*) AS n FROM items WHERE author_id IS NOT NULL "
                    "GROUP BY author_id ORDER BY n DESC LIMIT ?",
                    (limit,),
                )
                counts = {row["author_id"]: row["n"] for row in rows}
            current = await self._current(connection, list(counts))
            hits += [ProfileHit(_ref(platform, row), row["bio"], counts[row["profile_id"]]) for row in current]
        hits.sort(key=lambda hit: hit.items, reverse=True)
        return hits[:limit]

    async def _item_counts(self, connection: aiosqlite.Connection, profile_ids: list[str]) -> dict[str, int]:
        if not profile_ids:
            return {}
        rows = await connection.execute_fetchall(
            f"SELECT author_id, count(*) AS n FROM items WHERE author_id IN ({','.join('?' * len(profile_ids))}) "
            "GROUP BY author_id",
            profile_ids,
        )
        counts = {profile_id: 0 for profile_id in profile_ids}
        counts.update({row["author_id"]: row["n"] for row in rows})
        return counts

    async def image(self, platform: str, image_id: int) -> Path | None:
        connection = await self.archives.connection(platform)
        if connection is None:
            return None
        rows = await connection.execute_fetchall("SELECT path FROM profile_images WHERE rowid = ?", (image_id,))
        return Path(rows[0]["path"]) if rows and rows[0]["path"] else None
