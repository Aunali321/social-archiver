"""Reranking from an OpenAI-compatible server, the same shape vLLM and Jina both serve."""

from typing import Any

import httpx

from social_archiver.core import config


def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[dict[str, Any]]:
    """Rerank documents by relevance, returning dicts with 'index' and 'relevance_score',
    best first. Empty when no RERANK_URL is configured, so search falls back to vector order."""
    if not documents or not config.RERANK_URL:
        return []

    payload: dict[str, Any] = {"model": config.RERANK_MODEL, "query": query, "documents": documents}
    if top_n:
        payload["top_n"] = top_n

    response = httpx.post(config.RERANK_URL, json=payload, timeout=config.EMBEDDING_TIMEOUT)
    response.raise_for_status()
    return response.json()["results"]
