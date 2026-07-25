"""Parse a Reddit account data export (https://www.reddit.com/settings/data-request)
into seed references per category. The export lists the complete saved / vote / own
history that the listing API caps at 1000 items; the archiver hydrates the fullnames
in batches. Accepts the raw .zip or an extracted directory."""

import csv
import io
import logging
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeedRef:
    fullname: str
    permalink: str


# (csv filename, fullname prefix, fixed category or None when it comes from `direction`)
_SOURCES = (
    ("saved_posts.csv", "t3_", "saved"),
    ("saved_comments.csv", "t1_", "saved"),
    ("post_votes.csv", "t3_", None),
    ("comment_votes.csv", "t1_", None),
    ("posts.csv", "t3_", "own"),
    ("comments.csv", "t1_", "own"),
)

_VOTE_CATEGORY = {"up": "upvoted", "down": "downvoted"}


def parse_export(path: Path) -> dict[str, list[SeedRef]]:
    seeds: dict[str, dict[str, SeedRef]] = {}
    for name, prefix, fixed_category in _SOURCES:
        for row in _read_csv(path, name):
            raw_id = (row.get("id") or "").strip()
            if not raw_id:
                continue
            category = fixed_category or _VOTE_CATEGORY.get((row.get("direction") or "").strip().lower())
            if category is None:
                continue
            fullname = prefix + raw_id
            seeds.setdefault(category, {})[fullname] = SeedRef(fullname, _permalink(row, prefix, raw_id))

    result = {category: list(refs.values()) for category, refs in seeds.items()}
    logger.info("Export parsed: " + ", ".join(f"{cat}={len(refs)}" for cat, refs in result.items()))
    return result


def _permalink(row: dict[str, str], prefix: str, raw_id: str) -> str:
    permalink = (row.get("permalink") or "").strip()
    if permalink.startswith("http"):
        return permalink
    if permalink.startswith("/"):
        return f"https://www.reddit.com{permalink}"
    return f"https://redd.it/{raw_id}" if prefix == "t3_" else f"https://www.reddit.com/comments/{raw_id}"


def _read_csv(path: Path, name: str) -> Iterator[dict[str, str]]:
    if path.is_dir():
        file = path / name
        if not file.exists():
            return
        with file.open(newline="", encoding="utf-8-sig") as handle:
            yield from csv.DictReader(handle)
        return

    with zipfile.ZipFile(path) as archive:
        member = next((n for n in archive.namelist() if n == name or n.endswith(f"/{name}")), None)
        if member is None:
            return
        with archive.open(member) as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
