import logging

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading Qwen3-Embedding-0.6B...")
        _model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
        logger.info("Model loaded")
    return _model


def embed_query(text: str) -> list[float]:
    return _get_model().encode(text, prompt_name="query").tolist()


def embed_document(text: str) -> list[float]:
    return _get_model().encode(text).tolist()


def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    return _get_model().encode(texts, batch_size=batch_size, show_progress_bar=True).tolist()
