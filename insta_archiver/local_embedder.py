import logging
from typing import List, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embeddinggemma-300m...")
        _model = SentenceTransformer(
            "google/embeddinggemma-300m",
            trust_remote_code=True,
        )
        logger.info("Model loaded")
    return _model


def embed_query(text: str) -> List[float]:
    """Embed a search query."""
    model = _get_model()
    embedding = model.encode_query(text)
    return embedding.tolist()


def embed_document(text: str, title: Optional[str] = None) -> List[float]:
    """Embed a document for indexing."""
    model = _get_model()
    embedding = model.encode_document(text)
    return embedding.tolist()


def embed_documents(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """Embed multiple documents."""
    model = _get_model()
    embeddings = model.encode_document(texts, batch_size=batch_size, show_progress_bar=True)
    return embeddings.tolist()
