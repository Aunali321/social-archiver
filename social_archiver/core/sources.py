"""Archiving a source: an account or a subreddit, taken in its own right.

The categories archive what you interacted with, and reach outwards from there. A source is the
opposite direction: name it, and everything it has posted is fetched, whether or not you ever
touched any of it.

That difference is why this does not reuse `TweetExpander` and its equivalents. Expansion exists
because a like gives you one tweet out of a conversation and the rest has to be discovered. A
source walk already returns everything the account posted, so a thread is reconstructed from the
`in_reply_to` fields already in hand, at no request cost.

A platform supplies a `SourceFetcher`; everything below it is shared. The fetcher yields batches
because that is the unit this commits in: the walk of a large account is long enough that holding
it all until the end would put hours of work behind a single interrupt.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import aiosqlite

from social_archiver.core.database import ArchiveStatus, Database, Item
from social_archiver.core.jobs import PlatformPort, download_pending

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Which source, on which platform. `kind` distinguishes the shapes a platform offers —
    an account and a subreddit are both sources, and nothing else about them is alike."""

    platform: str
    kind: str
    target: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.target}"


@dataclass(slots=True)
class TrackedSource:
    """A source the scheduler re-walks, as opposed to one run once from the UI."""

    platform: str
    kind: str
    target: str
    enabled: bool
    added_at: datetime
    last_run: datetime | None = None

    @property
    def ref(self) -> SourceRef:
        return SourceRef(self.platform, self.kind, self.target)

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "TrackedSource":
        def when(name: str) -> datetime | None:
            return datetime.fromisoformat(row[name]) if row[name] else None

        return cls(
            platform=row["platform"],
            kind=row["kind"],
            target=row["target"],
            enabled=bool(row["enabled"]),
            added_at=when("added_at"),
            last_run=when("last_run"),
        )


class SourceFetcher(Protocol):
    """What a platform has to provide. Everything else here is platform-agnostic."""

    kinds: tuple[str, ...]

    def category(self, ref: SourceRef) -> str:
        """The archive category rows land in, which decides their downloads folder and
        whether upload and embed pick them up."""
        ...

    def walk(self, ref: SourceRef, since: str | None) -> AsyncIterator[Sequence[Item]]:
        """Yield batches of items, newest first, stopping at `since` when it is given.

        Newest first is what makes a watermark work: the first item of the first batch is the
        newest the source has, so a later run resumes from there rather than paging to the
        start again."""
        ...

    def lacks_content(self, text: str | None, archive_status: ArchiveStatus) -> bool:
        """Whether this is a record of an item rather than the item, so a source that still has
        the real thing is allowed to overwrite it.

        Platforms disagree on how they say so. One refuses the item and the archive records a
        status; another serves the item with a marker where the text used to be, and the row
        looks archived. Only the platform can read its own signal."""
        ...


class SourceJob:
    """Walks a source, records what it finds, and downloads its media.

    Commits per batch, so an interrupted walk keeps everything up to the last one and resumes
    from the watermark rather than starting over."""

    def __init__(self, db: Database, port: PlatformPort, fetcher: SourceFetcher):
        self.db = db
        self.port = port
        self.fetcher = fetcher

    async def run(self, ref: SourceRef, since: str | None = None, download: bool = True) -> int:
        if ref.kind not in self.fetcher.kinds:
            raise ValueError(f"{ref.platform} has no source kind {ref.kind!r}; it offers {self.fetcher.kinds}")

        category = self.fetcher.category(ref)
        logger.info(f"Archiving {ref} ({'full walk' if since is None else f'since {since}'})")

        recorded = repaired = 0
        async for batch in self.fetcher.walk(ref, since):
            if not batch:
                continue
            held = await self.db.held([item.item_id for item in batch])
            fresh = [item for item in batch if item.item_id not in held]
            for item in fresh:
                item.category = category
                item.source_target = ref.target
            await self.db.insert_many(fresh)

            # Rows holding a record of an item the platform had already dropped, which this
            # source still has. Sources capture on their own schedule rather than on request,
            # so they outlive a deletion applied since. Requiring more text than is stored is
            # what keeps a restore from trading one placeholder for a shorter one.
            repaired += await self.db.restore_content(
                [
                    item
                    for item in batch
                    if (row := held.get(item.item_id)) is not None
                    and self.fetcher.lacks_content(row.text, row.archive_status)
                    and not self.fetcher.lacks_content(item.text, item.archive_status)
                    and len(item.text or "") > len(row.text or "")
                ]
            )

            # Everything else the walk returned is already archived, almost always because the
            # account was liked before it was tracked. Those rows keep the category and the
            # media folder they were first stored under — one copy of a file, not two — but
            # they still belong to this source, so both are recorded rather than skipped.
            if held:
                await self.db.add_categories((item_id, category) for item_id in held)
                await self.db.attribute_to_source(self.port.platform, held, ref.target)

            recorded += len(fresh)
            logger.info(
                f"{ref}: recorded {len(fresh)} of {len(batch)}"
                f"{f', attributed {len(held)} already held' if held else ''} ({recorded} so far)"
            )

        logger.info(f"{ref}: {recorded} new item(s)" + (f", {repaired} deleted item(s) recovered" if repaired else ""))
        if not download:
            # Recorded with their media urls and left pending, which is what a later run, or
            # any other job that downloads, picks them up from.
            logger.info(f"{ref}: media not downloaded, left pending")
            return recorded
        # After the walk rather than per batch: media is on a CDN with no rate limit, so there
        # is nothing to gain by interleaving, and one pass keeps the walk's pacing predictable.
        await download_pending(self.db, self.port)
        return recorded

    async def watermark_after(self, ref: SourceRef) -> str | None:
        """The newest id held for a source, which is where the next delta walk stops."""
        return await self.db.newest_source_item(self.port.platform, ref.target)
