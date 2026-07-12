import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading Qwen3-Embedding-0.6B...")
        _model = SentenceTransformer(
            "Qwen/Qwen3-Embedding-0.6B",
        )
        logger.info("Model loaded")
    return _model


def embed_query(text: str) -> List[float]:
    """Embed a search query."""
    model = _get_model()
    embedding = model.encode(text, prompt_name="query")
    return embedding.tolist()


def embed_document(text: str, title: Optional[str] = None) -> List[float]:
    """Embed a document for indexing."""
    model = _get_model()
    embedding = model.encode(text)
    return embedding.tolist()


def embed_documents(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """Embed multiple documents."""
    model = _get_model()
    embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=True)
    return embeddings.tolist()
