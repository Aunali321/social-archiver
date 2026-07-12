import logging
from typing import Any

import torch

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from transformers import AutoModel

        logger.info("Loading jina-reranker-v3...")
        _model = AutoModel.from_pretrained("jinaai/jina-reranker-v3", dtype="auto", trust_remote_code=True)
        _model.eval()
        _model.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
        logger.info("Reranker loaded")
    return _model


def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[dict[str, Any]]:
    """Rerank documents by relevance to query. Returns dicts with 'document', 'relevance_score', 'index'."""
    if not documents:
        return []
    return _get_model().rerank(query, documents, top_n=top_n)
