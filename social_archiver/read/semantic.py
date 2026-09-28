"""Semantic search over the per-platform Milvus stores, hydrated from the archives.

Optional twice over: embedding must be enabled and a platform must actually have been
embedded. `available()` is what the UI checks before offering it; asking anyway raises,
because an empty answer would read as "nothing matches"."""

import asyncio
import importlib
from dataclasses import dataclass
from pathlib import Path

from social_archiver.core import config
from social_archiver.core.config import PLATFORMS, ConfigError
from social_archiver.core.database import Item
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import embed_client
from social_archiver.read.models import ItemFilters
from social_archiver.read.query import parse
from social_archiver.read.store import ArchiveReader


@dataclass(slots=True)
class SemanticHit:
    platform: str
    item_id: str
    score: float
    caption: str | None
    media_type: str | None
    resource_index: int | None


def _collections(platform: str) -> dict[str, str]:
    return importlib.import_module(f"social_archiver.platforms.{platform}.service").MILVUS_COLLECTIONS


def _uri(platform: str) -> str:
    platform_config = importlib.import_module(f"social_archiver.platforms.{platform}.config")
    return getattr(platform_config, f"{platform.upper()}_MILVUS_URI")


def available(platforms: tuple[str, ...] = PLATFORMS) -> list[str]:
    """Platforms whose vector store exists. A server URI is assumed reachable; a Milvus Lite
    file is checked on disk, since an empty path would create one where none belongs."""
    if not config.EMBEDDING_ENABLED:
        return []
    ready = []
    for platform in platforms:
        uri = _uri(platform)
        if uri.startswith(("http://", "https://", "tcp://")) or Path(uri).exists():
            ready.append(platform)
    return ready


def _search_one(platform: str, vector: list[float], query: str, limit: int) -> list[SemanticHit]:
    collections = _collections(platform)
    manager = MilvusManager(_uri(platform), collections)
    return [
        SemanticHit(
            platform=platform,
            item_id=hit["item_id"],
            score=hit["score"],
            caption=hit.get("caption") or None,
            media_type=hit.get("media_type") or None,
            resource_index=None if hit.get("resource_index", -1) == -1 else hit["resource_index"],
        )
        for category in collections
        for hit in manager.hybrid_search(category, vector, query, limit=limit, rrf_k=config.SEARCH_RRF_K)
    ]


async def search(query: str, platforms: tuple[str, ...], limit: int = 20) -> list[SemanticHit]:
    """Best `limit` across the asked-for platforms, deduplicated to one hit per item (an
    Instagram album embeds per file, and the UI links to items, not vectors)."""
    ready = [p for p in available() if not platforms or p in platforms]
    if not ready:
        raise ConfigError(
            "semantic search is unavailable: EMBEDDING_ENABLED is off or nothing has been embedded"
            + (f" for {', '.join(platforms)}" if platforms else "")
            + "; use full-text search"
        )
    vector = await asyncio.to_thread(embed_client.embed_query, query)
    batches = await asyncio.gather(*(asyncio.to_thread(_search_one, p, vector, query, limit) for p in ready))
    merged = sorted((hit for batch in batches for hit in batch), key=lambda hit: hit.score, reverse=True)
    seen: set[tuple[str, str]] = set()
    unique = []
    for hit in merged:
        key = (hit.platform, hit.item_id)
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique[:limit]


async def hydrate(
    reader: ArchiveReader, hits: list[SemanticHit], filters: ItemFilters
) -> list[tuple[SemanticHit, Item]]:
    """Hits with their archived items, in rank order. The vector store knows nothing of the
    archive's filters, so hits whose item fails them drop out here."""
    held = {
        platform: await reader.get_many(platform, {h.item_id for h in hits if h.platform == platform}, filters)
        for platform in {h.platform for h in hits}
        if not filters.platforms or platform in filters.platforms
    }
    return [(hit, item) for hit in hits if (item := held.get(hit.platform, {}).get(hit.item_id))]


async def find(
    reader: ArchiveReader, query: str, filters: ItemFilters, limit: int = 20
) -> list[tuple[SemanticHit, Item]]:
    """The whole semantic query: operators become filters (see read.query), only the free
    words are embedded, and hits that fail the filters drop out."""
    parsed = parse(query, filters)
    if not parsed.text:
        raise ValueError("semantic search needs words to search for, not only operators")
    return await hydrate(reader, await search(parsed.text, parsed.filters.platforms, limit=limit), parsed.filters)
