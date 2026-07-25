from typing import Any

from social_archiver.core import config


class VLMConfigError(Exception):
    """Raised when the configured VLM provider is missing required credentials."""


def create_vlm_client(provider: str) -> tuple[Any, str]:
    """Instantiate the configured VLM client. Returns (client, model_name)."""
    provider = provider.lower()

    if provider == "vertex":
        from social_archiver.llm.vertex_client import VertexVLMClient

        client = VertexVLMClient(
            model=config.VERTEX_MODEL,
            project=config.VERTEX_PROJECT,
            location=config.VERTEX_LOCATION,
            timeout=config.EMBEDDING_TIMEOUT,
        )
        return client, config.VERTEX_MODEL

    if provider == "gemini":
        if not config.GEMINI_API_KEY:
            raise VLMConfigError("VLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
        from social_archiver.llm.gemini_client import GeminiClient

        client = GeminiClient(
            api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL, timeout=config.EMBEDDING_TIMEOUT
        )
        return client, config.GEMINI_MODEL

    if not config.OPENROUTER_API_KEY:
        raise VLMConfigError("EMBEDDING_ENABLED is true but OPENROUTER_API_KEY is not set")
    from social_archiver.llm.vlm_client import VLMClient

    client = VLMClient(api_key=config.OPENROUTER_API_KEY, vlm_model=config.VLM_MODEL, timeout=config.EMBEDDING_TIMEOUT)
    return client, config.VLM_MODEL
