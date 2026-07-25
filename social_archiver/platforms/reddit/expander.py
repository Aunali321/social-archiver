"""Expand seed items (saved/voted/own posts and comments) into bounded context:
a seed comment pulls in its submission and its ancestor comment chain, never the
whole comment tree. Combining up- and down-voted comments this way reconstructs the
connected subtrees the user engaged with."""

import logging

from social_archiver.platforms.reddit.client import RedditClient
from social_archiver.platforms.reddit.simple_post import RedditItem

logger = logging.getLogger(__name__)

MAX_DEPTH = 32  # safety cap on how far an ancestor chain is walked


class ThreadExpander:
    def __init__(self, client: RedditClient):
        self.client = client

    async def expand(self, seeds: list[RedditItem]) -> list[RedditItem]:
        known: dict[str, RedditItem] = {seed.fullname: seed for seed in seeds}

        frontier: dict[str, str] = {}  # missing fullname -> the child that referenced it
        for seed in seeds:
            if seed.is_tombstone or seed.kind != "comment":
                continue
            _want(frontier, known, seed.submission_fullname, seed.fullname)
            _want(frontier, known, seed.parent_fullname, seed.fullname)

        for _ in range(MAX_DEPTH):
            if not frontier:
                break
            fetched = await self.client.hydrate(list(frontier))
            next_frontier: dict[str, str] = {}
            for item in fetched:
                item.origin = "submission" if item.kind == "post" else "parent"
                item.discovered_via_fullname = frontier.get(item.fullname)
                known[item.fullname] = item
                if item.kind == "comment":
                    _want(next_frontier, known, item.parent_fullname, item.fullname)
                    _want(next_frontier, known, item.submission_fullname, item.fullname)
            frontier = next_frontier

        return list(known.values())


def _want(
    frontier: dict[str, str],
    known: dict[str, RedditItem],
    fullname: str | None,
    via: str,
):
    if fullname and fullname not in known and fullname not in frontier:
        frontier[fullname] = via
