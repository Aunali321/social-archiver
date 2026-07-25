"""Embeddings from an OpenAI-compatible server (vLLM, llama.cpp, TEI, ...).

Run the model wherever it has the hardware for it — embedding a 1.74B multimodal model
is ~5 TFLOP per image, which is hours of CPU for an archive of any size.
"""

import httpx

from social_archiver.core import config


def _embed(text: str) -> list[float]:
    response = httpx.post(
        config.EMBED_URL,
        json={"model": config.EMBED_MODEL, "input": [text]},
        timeout=config.EMBEDDING_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def embed_query(text: str) -> list[float]:
    return _embed(config.EMBED_QUERY_PREFIX + text)


def embed_document(text: str) -> list[float]:
    return _embed(config.EMBED_DOC_PREFIX + text)
