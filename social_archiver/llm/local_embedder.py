import logging

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading jina-embeddings-v5-omni-small...")
        _model = SentenceTransformer(
            "jinaai/jina-embeddings-v5-omni-small",
            trust_remote_code=True,
            model_kwargs={"default_task": "retrieval"},
        )
        logger.info("Model loaded")
    return _model


def embed_query(text: str) -> list[float]:
    return _get_model().encode_query(text).tolist()


def embed_document(text: str) -> list[float]:
    return _get_model().encode_document(text).tolist()


def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    return _get_model().encode_document(texts, batch_size=batch_size, show_progress_bar=True).tolist()
