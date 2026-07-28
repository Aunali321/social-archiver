"""Archiving a subreddit in full, from an Arctic Shift dump.

Reddit's listings stop at about a thousand items however they are paged, so a subreddit cannot
be walked through the API at all. Arctic Shift (https://arctic-shift.photon-reddit.com)
publishes the whole history as JSONL, one record per line in the shape the API returns, which
is why nothing here hydrates: every field the archive stores is already on the line.

The dump is also the only place some of this content still exists. It captures a post when it
is made, so one removed later keeps its body here long after Reddit stops serving it. Measured
against the live archive on r/LocalLLaMA: of 1,802 posts held under both, 106 are record-only
here and complete in the dump, and 70 of those recover the author's name too. Nothing went the
other way, which is why the ingest is allowed to fill a tombstone.

`since` is ignored. It exists for the API walks, where paging back costs requests; re-reading a
local file costs nothing, and dedupe by id already resumes an interrupted ingest exactly.
"""

import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path

from social_archiver.core.database import ArchiveStatus, Item
from social_archiver.core.sources import SourceRef
from social_archiver.platforms.reddit.client import raw_media
from social_archiver.platforms.reddit.simple_post import RedditItem

logger = logging.getLogger(__name__)

ORIGIN = "subreddit"
BATCH = 1000

# A body Reddit replaced with a marker. The record's title, author, score and permalink are
# still real, so the row is worth keeping; the marker is not worth storing as content.
_REMOVED = frozenset({"[removed]", "[deleted]"})

# The titles Reddit substitutes when the post itself is gone, rather than just its body.
_REMOVED_TITLES = frozenset({"[deleted by user]", "[ removed by moderator ]", "[ removed by reddit ]"})


class RedditSourceFetcher:
    kinds = ("subreddit",)

    def __init__(self, dump_dir: Path):
        self.dump_dir = dump_dir

    def category(self, ref: SourceRef) -> str:
        """Each subreddit gets its own category, so its media lands in its own folder and it
        can be uploaded or embedded independently of the others."""
        return f"subreddit-{ref.target.lower()}"

    def lacks_content(self, text: str | None, archive_status: ArchiveStatus) -> bool:
        """Reddit serves a removed item rather than refusing it, so the row looks archived and
        the loss shows only in the text. Three shapes, all present in the archive: a comment
        whose whole body is the marker, a post whose title is a removal notice, and a post
        whose real title survived while its body was replaced. That last one is why this reads
        the parts rather than matching the whole string."""
        if not text:
            return True
        title, _, body = text.strip().partition("\n\n")
        if title in _REMOVED:
            return True
        if body and body.strip() in _REMOVED:
            return True
        return title.strip("* ").lower() in _REMOVED_TITLES

    async def walk(self, ref: SourceRef, since: str | None) -> AsyncIterator[Sequence[Item]]:
        category = self.category(ref)
        found = False
        for kind, parse in (("post", _post), ("comment", _comment)):
            path = self._dump(ref, kind)
            if path is None:
                continue
            found = True
            logger.info(f"{ref}: reading {path.name} ({path.stat().st_size / 1e6:,.0f} MB)")
            for batch in _batches(path):
                yield [parse(raw).to_item(category) for raw in batch]

        if not found:
            raise FileNotFoundError(
                f"No dump for r/{ref.target} in {self.dump_dir}; expected "
                f"{_filename(ref.target, 'post')} and/or {_filename(ref.target, 'comment')}"
            )

    def _dump(self, ref: SourceRef, kind: str) -> Path | None:
        path = self.dump_dir / _filename(ref.target, kind)
        return path if path.exists() else None


def _filename(target: str, kind: str) -> str:
    return f"r_{target.lower()}_{kind}s.jsonl"


def _batches(path: Path) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for record in _records(path):
        batch.append(record)
        if len(batch) == BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def _records(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.endswith("\n"):
                # Half a record, because the dump is still downloading. Stopping here rather
                # than failing is what lets an in-progress dump be ingested; the next run
                # reads this line whole, along with everything written since.
                logger.info(f"{path.name}: incomplete final line, dump is still being written")
                return
            yield json.loads(line)


def _body(text: str | None) -> str | None:
    return None if not text or text in _REMOVED else text


def _permalink(raw: dict) -> str:
    return f"https://www.reddit.com{raw['permalink']}"


def _created(raw: dict) -> datetime:
    return datetime.fromtimestamp(raw["created_utc"], tz=timezone.utc)


def _post(raw: dict) -> RedditItem:
    return RedditItem(
        fullname=raw["name"],
        kind="post",
        author=raw["author"],
        subreddit=raw["subreddit"],
        permalink=_permalink(raw),
        created_at=_created(raw),
        score=raw.get("score"),
        title=raw.get("title"),
        body=_body(raw.get("selftext")),
        num_comments=raw.get("num_comments"),
        over_18=bool(raw.get("over_18")),
        link_flair=raw.get("link_flair_text"),
        # A post roots its own thread, which is what the graph columns hang off for its comments
        submission_fullname=raw["name"],
        link_url=None if raw.get("is_self") else (raw.get("url") or None),
        media=raw_media(raw, "post"),
        origin=ORIGIN,
    )


def _comment(raw: dict) -> RedditItem:
    return RedditItem(
        fullname=raw["name"],
        kind="comment",
        author=raw["author"],
        subreddit=raw["subreddit"],
        permalink=_permalink(raw),
        created_at=_created(raw),
        score=raw.get("score"),
        body=_body(raw.get("body")),
        submission_fullname=raw.get("link_id"),
        parent_fullname=raw.get("parent_id"),
        media=raw_media(raw, "comment"),
        origin=ORIGIN,
    )
