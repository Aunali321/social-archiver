"""
Search Twitter embeddings using hybrid search (semantic + BM25).
Usage: python search_twitter_embeddings.py "search query" [--category bookmarks|likes]
"""
import argparse
import sys

from twitter_archiver import config
from twitter_archiver.milvus_manager import MilvusManager
from insta_archiver import local_embedder
from insta_archiver.reranker import rerank


def main():
    parser = argparse.ArgumentParser(description="Search Twitter embeddings")
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--category",
        choices=["bookmarks", "likes"],
        default="bookmarks",
        help="Category to search",
    )
    parser.add_argument("--top-k", type=int, default=config.SEARCH_HYBRID_TOPK)
    parser.add_argument("--rerank-top-n", type=int, default=config.SEARCH_RERANK_TOPN)
    parser.add_argument("--no-rerank", action="store_true")

    args = parser.parse_args()

    milvus_manager = MilvusManager(uri=config.TWITTER_MILVUS_URI)

    print(f"Searching '{args.query}' in {args.category}...\n")

    query_embedding = local_embedder.embed_query(args.query)

    results = milvus_manager.hybrid_search(
        category=args.category,
        query_embedding=query_embedding,
        query_text=args.query,
        limit=args.top_k,
        rrf_k=config.SEARCH_RRF_K,
    )

    if not results:
        print("No results found.")
        milvus_manager.close()
        return

    if not args.no_rerank:
        documents = [r.get("text", "") for r in results]
        reranked = rerank(args.query, documents, top_n=args.rerank_top_n)

        print(f"Top {len(reranked)} results (reranked):\n")
        for i, item in enumerate(reranked):
            original_idx = item["index"]
            result = results[original_idx]
            score = item["relevance_score"]
            username = result.get("username", "?")
            tweet_id = result.get("tweet_id", "?")
            text_preview = (result.get("tweet_text") or "")[:200]

            print(f"  {i+1}. [{score:.4f}] @{username}")
            print(f"     https://x.com/{username}/status/{tweet_id}")
            print(f"     {text_preview}")
            print()
    else:
        print(f"Top {min(args.rerank_top_n, len(results))} results:\n")
        for i, result in enumerate(results[: args.rerank_top_n]):
            username = result.get("username", "?")
            tweet_id = result.get("tweet_id", "?")
            score = result.get("score", 0)
            text_preview = (result.get("tweet_text") or "")[:200]

            print(f"  {i+1}. [{score:.4f}] @{username}")
            print(f"     https://x.com/{username}/status/{tweet_id}")
            print(f"     {text_preview}")
            print()

    milvus_manager.close()


if __name__ == "__main__":
    main()
