import logging
import torch
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from transformers import AutoModel

        logger.info("Loading jina-reranker-v3...")
        _model = AutoModel.from_pretrained(
            "jinaai/jina-reranker-v3",
            dtype="auto",
            trust_remote_code=True,
        )
        logger.info("Model loaded, setting eval mode...")
        _model.eval()
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"Setting device to {device}...")
        _model.to(device)
        logger.info("Reranker loaded")
    return _model


def rerank(
    query: str,
    documents: List[str],
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Rerank documents by relevance to query.
    Returns list of dicts with 'document', 'relevance_score', 'index'.
    """
    if not documents:
        return []

    logger.info(f"Reranking {len(documents)} documents...")
    model = _get_model()
    logger.info("Model obtained, calling rerank...")
    results = model.rerank(query, documents, top_n=top_n)
    logger.info(f"Reranking complete, got {len(results)} results")
    return results
