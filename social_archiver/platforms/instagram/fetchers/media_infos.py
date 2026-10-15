"""Resolving posts by id in bulk. `media/infos/` answers MEDIA_INFO_BATCH posts in one request;
instagrapi only exposes `media/{pk}/info/`, which would be a request each."""

import logging

from social_archiver.platforms.instagram.client import InstagramClient

logger = logging.getLogger(__name__)

MEDIA_INFO_BATCH = 50


def resolve_media(ig_client: InstagramClient, pks: list[str]) -> list[dict]:
    """Media the account can no longer see (deleted, or the account went private) is absent
    from the response."""
    resolved: list[dict] = []
    for start in range(0, len(pks), MEDIA_INFO_BATCH):
        chunk = pks[start : start + MEDIA_INFO_BATCH]
        items = _resolve_chunk(ig_client, chunk)
        if len(items) < len(chunk):
            logger.warning(f"{len(chunk) - len(items)} of {len(chunk)} posts no longer available")
        resolved.extend(items)
    return resolved


def _resolve_chunk(ig_client: InstagramClient, chunk: list[str]) -> list[dict]:
    """Instagram answers a batch holding one unresolvable id with an empty response for the
    *whole* batch, so a single bad id silently costs every id sent with it. Splitting on an empty
    response isolates the bad one instead of losing the batch, and costs the extra requests only
    when there is one."""
    items = ig_client.private_request("media/infos/", {"media_ids": ",".join(chunk)}).get("items", [])
    if items:
        return items
    if len(chunk) == 1:
        logger.warning(f"Post {chunk[0]} could not be resolved")
        return []
    mid = len(chunk) // 2
    return _resolve_chunk(ig_client, chunk[:mid]) + _resolve_chunk(ig_client, chunk[mid:])
