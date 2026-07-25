"""Parse the official Twitter/X data export (Settings -> Download an archive of your
data) into shallow seed stubs for the likes backfill. `like.js` holds every like id
ever, past the Likes-timeline cap; the expander resolves each stub to full data (or
keeps the stub's text if the tweet is gone). Bookmarks are not included in exports."""

import json
import logging
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_like_export(path: Path) -> list[dict[str, Any]]:
    seeds: dict[str, dict[str, Any]] = {}
    for raw in _read_like_files(path):
        array = raw[raw.index("[") : raw.rindex("]") + 1]
        for entry in json.loads(array):
            like = entry.get("like") or {}
            if tweet_id := like.get("tweetId"):
                seeds[tweet_id] = {"id": tweet_id, "text": like.get("fullText", ""), "shallow": True}
    logger.info(f"Twitter export parsed: {len(seeds)} liked tweet ids")
    return list(seeds.values())


def _read_like_files(path: Path):
    if path.is_dir():
        for file in sorted((path / "data").glob("like*.js")):
            yield file.read_text(encoding="utf-8")
        return
    with zipfile.ZipFile(path) as archive:
        for name in sorted(n for n in archive.namelist() if Path(n).name.startswith("like") and n.endswith(".js")):
            yield archive.read(name).decode("utf-8")
