"""Incremental top-up from the live listing API. Capped at ~1000 items by Reddit,
but only ever walks the recent delta: it stops at the first already-archived item
since saved/vote/overview listings are newest-first. The export backfills the rest."""

import logging

from social_archiver.platforms.reddit.client import RedditClient
from social_archiver.platforms.reddit.simple_post import RedditItem

logger = logging.getLogger(__name__)


async def fetch_live(client: RedditClient, category: str, known_ids: set[str] | None) -> list[RedditItem]:
    items: list[RedditItem] = []
    async for item in client.listing(category, limit=None):
        if known_ids is not None and item.fullname in known_ids:
            break
        item.origin = category
        items.append(item)
    logger.info(f"Live {category}: {len(items)} new")
    return items
