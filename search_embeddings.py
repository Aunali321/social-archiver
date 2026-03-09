#!/usr/bin/env python3
"""
CLI utility for searching Instagram archive using hybrid search + reranking.
"""

import argparse
import asyncio
import sys
import logging
from insta_archiver import config
from insta_archiver import local_embedder
from insta_archiver import reranker
from insta_archiver.milvus_manager import MilvusManager
from insta_archiver.database import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def search(query: str, category: str, limit: int):
    """Search using hybrid (semantic + BM25) with reranking."""
    print(f"Searching for: '{query}'")
    print(f"Category: {category}")
    print("-" * 50)

    # Initialize Milvus
    milvus = MilvusManager(uri=config.INSTAGRAM_MILVUS_URI)

    # Generate query embedding
    print("Generating query embedding...")
    query_embedding = local_embedder.embed_query(query)
    print("Query embedding generated")

    # Hybrid search
    print(f"Running hybrid search (top {config.SEARCH_HYBRID_TOPK})...")
    candidates = milvus.hybrid_search(
        category=category,
        query_embedding=query_embedding,
        query_text=query,
        limit=config.SEARCH_HYBRID_TOPK,
        rrf_k=config.SEARCH_RRF_K,
    )
    print(f"Hybrid search complete, {len(candidates)} candidates")

    if not candidates:
        print("No results found")
        milvus.close()
        return

    print(f"Found {len(candidates)} candidates, reranking...")

    # Prepare documents for reranking (limit to 10)
    docs = [c.get("text", "") or c.get("caption", "") or "" for c in candidates[:10]]
    print(f"Prepared {len(docs)} documents for reranking")

    # Rerank
    reranked = reranker.rerank(query, docs, top_n=limit)

    print(f"\nTop {len(reranked)} results:\n")

    # Display results with DB info
    asyncio.run(_display_results(reranked, candidates))

    milvus.close()


async def _display_results(reranked, candidates):
    """Display reranked results with database info."""
    async with Database() as db:
        for i, item in enumerate(reranked, 1):
            idx = item["index"]
            candidate = candidates[idx]
            media_pk = candidate.get("media_pk")
            score = item["relevance_score"]

            cursor = await db._connection.execute(
                "SELECT * FROM processed_media WHERE media_pk = ?", (media_pk,)
            )
            row = await cursor.fetchone()

            print(f"{i}. Score: {score:.4f}")
            if row:
                row_dict = dict(row)
                print(f"   Author: @{row_dict['author_username']}")
                print(f"   URL: {row_dict['post_url']}")
                caption = row_dict["caption"][:100] if row_dict["caption"] else "N/A"
                print(f"   Caption: {caption}...")
                if row_dict.get("vlm_description"):
                    desc = row_dict["vlm_description"][:150]
                    print(f"   VLM: {desc}...")
            else:
                print(f"   Media PK: {media_pk} (not in DB)")
            print()


async def get_stats():
    """Show embedding statistics."""
    async with Database() as db:
        stats = await db.get_stats()

        print("\nEmbedding Statistics:\n")
        for category, data in stats.items():
            print(f"{category.upper()}:")
            print(f"  Total: {data['total']}")
            print(f"  Uploaded: {data['uploaded']}")
            print(f"  Embedded: {data.get('embedded', 0)}")
            coverage = (
                data.get("embedded", 0) / data["uploaded"] * 100
                if data["uploaded"] > 0
                else 0
            )
            print(f"  Coverage: {coverage:.1f}%\n")


def init_collections():
    """Initialize or recreate Milvus collections."""
    print("Initializing Milvus collections...")
    milvus = MilvusManager(uri=config.INSTAGRAM_MILVUS_URI)
    milvus.initialize_collections(recreate=True)
    print("Done! Collections created with hybrid search schema.")
    milvus.close()


def main():
    print("DEBUG: main() called")
    parser = argparse.ArgumentParser(description="Search Instagram archive")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search by text")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument(
        "--category",
        "-c",
        choices=["likes", "saved", "shared"],
        default="saved",
        help="Category to search (default: saved)",
    )
    search_parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=10,
        help="Number of results (default: 10)",
    )

    # Stats command
    subparsers.add_parser("stats", help="Show statistics")

    # Init command
    subparsers.add_parser("init", help="Initialize Milvus collections")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "search":
        search(args.query, args.category, args.limit)
    elif args.command == "stats":
        asyncio.run(get_stats())
    elif args.command == "init":
        init_collections()


if __name__ == "__main__":
    main()
