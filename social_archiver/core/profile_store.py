"""Writing profiles: recording sightings, and the bookkeeping the image and refresh jobs need.

Shares the archive's connection. It is a part of `Database` rather than a database of its own,
because a sighting arrives with the item that carried it and has to land in the same file.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from social_archiver.core.profiles import VERSIONED, ImageKind, ImageRef, ProfileSnapshot


def _now() -> str:
    """The format sqlite's CURRENT_TIMESTAMP writes, which is what every seeded timestamp holds,
    so sightings and seeded history order correctly as plain strings."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _versioned(snapshot: ProfileSnapshot) -> dict[str, str | int | None]:
    def flag(value: bool | None) -> int | None:
        return None if value is None else int(value)

    return {
        "handle": snapshot.handle,
        "display_name": snapshot.display_name,
        "bio": snapshot.bio,
        "location": snapshot.location,
        "website": snapshot.website,
        "verified": flag(snapshot.verified),
        "private": flag(snapshot.private),
        "avatar_key": snapshot.avatar.key if snapshot.avatar else None,
        "banner_key": snapshot.banner.key if snapshot.banner else None,
    }


@dataclass(slots=True)
class UnobservedProfile:
    profile_id: str
    handle: str
    item_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PendingImage:
    profile_id: str
    key: str
    kind: ImageKind
    source: str | Path

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "PendingImage":
        source = row["source_url"] if row["source_url"] else Path(row["source_file"])
        return cls(row["profile_id"], row["key"], ImageKind(row["kind"]), source)


class ProfileStore:
    def __init__(self, connection: aiosqlite.Connection):
        self._connection = connection

    async def observe(self, platform: str, snapshots: Iterable[ProfileSnapshot]):
        """Record sightings, one transaction for the lot. An account seen twice in one batch is
        recorded once, from its last sighting: the batch is one moment, not a history."""
        latest = {snapshot.profile_id: snapshot for snapshot in snapshots}
        if not latest:
            return
        now = _now()
        for snapshot in latest.values():
            await self._observe(platform, snapshot, now)
        await self._connection.commit()

    async def _observe(self, platform: str, snapshot: ProfileSnapshot, now: str):
        await self._connection.execute(
            """
            INSERT INTO profiles (profile_id, platform, status, followers, following, posts,
                                  joined_at, first_seen, last_seen, observed_at)
            VALUES (:profile_id, :platform, COALESCE(:status, 'active'), :followers, :following,
                    :posts, :joined_at, :now, :now, :now)
            ON CONFLICT(profile_id) DO UPDATE SET
                status = COALESCE(:status, status),
                followers = COALESCE(:followers, followers),
                following = COALESCE(:following, following),
                posts = COALESCE(:posts, posts),
                joined_at = COALESCE(:joined_at, joined_at),
                last_seen = :now,
                observed_at = :now
            """,
            {
                "profile_id": snapshot.profile_id,
                "platform": platform,
                "status": snapshot.status,
                "followers": snapshot.followers,
                "following": snapshot.following,
                "posts": snapshot.posts,
                "joined_at": snapshot.joined_at.isoformat() if snapshot.joined_at else None,
                "now": now,
            },
        )
        await self._record_version(snapshot, now)
        for kind, image in ((ImageKind.AVATAR, snapshot.avatar), (ImageKind.BANNER, snapshot.banner)):
            if image:
                await self._record_image(snapshot.profile_id, kind, image)

    async def _record_version(self, snapshot: ProfileSnapshot, now: str):
        """A sighting that contradicts the latest version starts a new one, carrying forward
        whatever it did not say. One that contradicts nothing extends the latest version and
        fills in what it had not known: learning a field is not the account changing it."""
        observed = _versioned(snapshot)
        cursor = await self._connection.execute(
            "SELECT * FROM profile_versions WHERE profile_id = ? ORDER BY last_seen DESC, version_id DESC LIMIT 1",
            (snapshot.profile_id,),
        )
        latest = await cursor.fetchone()

        if latest and not any(
            value is not None and latest[name] is not None and value != latest[name] for name, value in observed.items()
        ):
            learned = {name: value for name, value in observed.items() if value is not None and latest[name] is None}
            assignments = "".join(f", {name} = :{name}" for name in learned)
            await self._connection.execute(
                f"UPDATE profile_versions SET last_seen = :now{assignments} WHERE version_id = :version_id",
                {**learned, "now": now, "version_id": latest["version_id"]},
            )
            return

        merged = {
            name: value if value is not None or latest is None else latest[name] for name, value in observed.items()
        }
        await self._connection.execute(
            f"""
            INSERT INTO profile_versions (profile_id, {", ".join(VERSIONED)}, first_seen, last_seen)
            VALUES (:profile_id, {", ".join(f":{name}" for name in VERSIONED)}, :now, :now)
            """,
            {**merged, "profile_id": snapshot.profile_id, "now": now},
        )

    async def _record_image(self, profile_id: str, kind: ImageKind, image: ImageRef):
        """A fresh sighting renews the source of a picture not yet fetched, which is what
        rescues one whose earlier link expired before the image job reached it."""
        match image.source:
            case Path() as file:
                url, file = None, str(file)
            case url:
                file = None
        await self._connection.execute(
            """
            INSERT INTO profile_images (profile_id, key, kind, source_url, source_file) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, key) DO UPDATE SET
                source_url = excluded.source_url, source_file = excluded.source_file, error = NULL
            WHERE path IS NULL
            """,
            (profile_id, image.key, kind, url, file),
        )

    async def pending_images(self, include_failed: bool) -> list[PendingImage]:
        cursor = await self._connection.execute(
            f"SELECT * FROM profile_images WHERE path IS NULL {'' if include_failed else 'AND error IS NULL'}"
        )
        return [PendingImage.from_row(row) for row in await cursor.fetchall()]

    async def mark_image_fetched(self, image: PendingImage, path: Path):
        await self._connection.execute(
            "UPDATE profile_images SET path = ?, error = NULL WHERE profile_id = ? AND key = ?",
            (str(path), image.profile_id, image.key),
        )
        await self._connection.commit()

    async def mark_image_failed(self, image: PendingImage, error: str):
        await self._connection.execute(
            "UPDATE profile_images SET error = ? WHERE profile_id = ? AND key = ?",
            (error, image.profile_id, image.key),
        )
        await self._connection.commit()

    async def unobserved(self) -> list[UnobservedProfile]:
        """Accounts no live payload has described yet, each with its archived items newest
        first: a refresh reads an account off one of its posts, and a deleted post means
        trying the next."""
        cursor = await self._connection.execute(
            """
            SELECT profiles.profile_id, items.item_id,
                   (SELECT handle FROM profile_versions
                    WHERE profile_versions.profile_id = profiles.profile_id
                    ORDER BY last_seen DESC, version_id DESC LIMIT 1) AS handle
            FROM profiles JOIN items ON items.author_id = profiles.profile_id
            WHERE profiles.observed_at IS NULL AND items.archive_status != 'tombstone'
            ORDER BY profiles.profile_id, items.created_at DESC
            """
        )
        unobserved: dict[str, UnobservedProfile] = {}
        for row in await cursor.fetchall():
            profile = unobserved.setdefault(row["profile_id"], UnobservedProfile(row["profile_id"], row["handle"]))
            profile.item_ids.append(row["item_id"])
        return list(unobserved.values())

    async def mark_self(self, profile_id: str):
        """The account the archive belongs to. A no-op until that account has authored something
        archived, since only then is there a profile to mark."""
        await self._connection.execute("UPDATE profiles SET is_self = 1 WHERE profile_id = ?", (profile_id,))
        await self._connection.commit()

    async def claim_authorless(self, profile_id: str, author_username: str):
        """Link the rows a store recorded under a placeholder name because it could not name the
        account, now that the account is known: WhatsApp's own messages, which no store keys
        by the jid the bridge later reports."""
        await self._connection.execute(
            "UPDATE items SET author_id = ? WHERE author_id IS NULL AND author_username = ?",
            (profile_id, author_username),
        )
        await self._connection.commit()
