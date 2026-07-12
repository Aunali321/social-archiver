#!/usr/bin/env python3
"""Hybrid (semantic + BM25) search + reranking over the archive.

Usage:
    uv run python scripts/search.py search "query" --platform instagram --category likes
    uv run python scripts/search.py stats --platform twitter
    uv run python scripts/search.py init --platform instagram
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_archiver.core import config
from social_archiver.core.database import Database
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import local_embedder, reranker
from social_archiver.platforms.instagram import config as instagram_config
from social_archiver.platforms.twitter import config as twitter_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

PLATFORMS = {
    "instagram": {
        "uri": instagram_config.INSTAGRAM_MILVUS_URI,
        "db_path": instagram_config.DATABASE_PATH,
        "collections": {"likes": "instagram_likes", "saved": "instagram_saved", "shared": "instagram_shared"},
    },
    "twitter": {
        "uri": twitter_config.TWITTER_MILVUS_URI,
        "db_path": twitter_config.DATABASE_PATH,
        "collections": {"likes": "twitter_likes", "bookmarks": "twitter_bookmarks"},
    },
}


def _milvus(platform: str) -> MilvusManager:
    p = PLATFORMS[platform]
    return MilvusManager(uri=p["uri"], collections=p["collections"])


def search(platform: str, query: str, category: str, limit: int, no_rerank: bool):
    print(f"Searching '{query}' in {platform}/{category}...\n")

    milvus = _milvus(platform)
    query_embedding = local_embedder.embed_query(query)

    candidates = milvus.hybrid_search(
        category=category,
        query_embedding=query_embedding,
        query_text=query,
        limit=max(config.SEARCH_HYBRID_TOPK, limit),
        rrf_k=config.SEARCH_RRF_K,
    )

    if not candidates:
        print("No results found.")
        milvus.close()
        return

    if no_rerank:
        asyncio.run(_display(platform, candidates[:limit]))
    else:
        docs = [c.get("text", "") or c.get("caption", "") or "" for c in candidates[:10]]
        reranked = reranker.rerank(query, docs, top_n=limit)
        asyncio.run(_display(platform, [candidates[item["index"]] for item in reranked]))

    milvus.close()


async def _display(platform: str, candidates: list[dict]):
    async with Database(PLATFORMS[platform]["db_path"]) as db:
        for i, candidate in enumerate(candidates, 1):
            item_id = candidate.get("item_id")
            row = await db.get_item(item_id) if item_id else None

            print(f"{i}. item_id={item_id}")
            if row:
                print(f"   Author: @{row['author_username']}")
                print(f"   URL: {row['post_url']}")
                text = (row.get("text") or "")[:150]
                print(f"   Text: {text}")
                if row.get("vlm_description"):
                    print(f"   VLM: {row['vlm_description'][:150]}")
            else:
                print(f"   Caption: {(candidate.get('caption') or '')[:150]} (not in DB)")
            print()


async def show_stats(platform: str):
    async with Database(PLATFORMS[platform]["db_path"]) as db:
        stats = await db.get_stats(platform)

        print(f"\n{platform} stats:\n")
        for category, data in stats.items():
            coverage = data["embedded"] / data["uploaded"] * 100 if data["uploaded"] else 0
            print(f"{category.upper()}: total={data['total']} uploaded={data['uploaded']} embedded={data['embedded']} ({coverage:.1f}%)")


def init_collections(platform: str):
    print(f"Initializing Milvus collections for {platform}...")
    milvus = _milvus(platform)
    milvus.initialize_collections(recreate=True)
    milvus.close()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Search the social-archiver archive")
    parser.add_argument("--platform", choices=list(PLATFORMS), required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search by text")
    search_parser.add_argument("query")
    search_parser.add_argument("--category", "-c", default="likes")
    search_parser.add_argument("--limit", "-n", type=int, default=10)
    search_parser.add_argument("--no-rerank", action="store_true")

    subparsers.add_parser("stats", help="Show statistics")
    subparsers.add_parser("init", help="Initialize/recreate Milvus collections")

    args = parser.parse_args()

    if args.command == "search":
        search(args.platform, args.query, args.category, args.limit, args.no_rerank)
    elif args.command == "stats":
        asyncio.run(show_stats(args.platform))
    elif args.command == "init":
        init_collections(args.platform)


if __name__ == "__main__":
    main()
