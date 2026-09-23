"""Embedding service: policy in front of a provider."""

from __future__ import annotations

from personal_context_mcp.config import Settings
from personal_context_mcp.embeddings.base import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    redact,
)
from personal_context_mcp.logging_setup import get_logger

logger = get_logger(__name__)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai" and settings.openai_api_key.get_secret_value():
        from personal_context_mcp.embeddings.remote import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            settings.openai_api_key.get_secret_value(), settings.embedding_model
        )
    if settings.embedding_provider == "gemini" and settings.gemini_api_key.get_secret_value():
        from personal_context_mcp.embeddings.remote import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider(
            settings.gemini_api_key.get_secret_value(), settings.embedding_model
        )
    if settings.embedding_provider != "none":
        logger.warning(
            "embedding provider configured without an API key; falling back to text search",
            extra={"embedding_provider": settings.embedding_provider},
        )
    return NullEmbeddingProvider()


class EmbeddingService:
    """Embeds text, refusing to send anything the privacy policy forbids."""

    def __init__(self, provider: EmbeddingProvider, settings: Settings) -> None:
        self._provider = provider
        self._settings = settings
        self._blocked = set(settings.no_external_sensitivity)
        self._external = not isinstance(provider, NullEmbeddingProvider)

    @property
    def enabled(self) -> bool:
        return self._external

    def allowed(self, sensitivity: str) -> bool:
        return not (self._external and sensitivity in self._blocked)

    async def embed_one(self, text: str, *, sensitivity: str = "normal") -> list[float] | None:
        results = await self.embed([text], sensitivity=sensitivity)
        return results[0] if results else None

    async def embed(
        self, texts: list[str], *, sensitivity: str = "normal"
    ) -> list[list[float] | None]:
        """Returns one vector per input, or None where embedding was skipped."""
        if not texts or not self._external:
            return [None] * len(texts)
        if not self.allowed(sensitivity):
            logger.info("embedding skipped by privacy policy", extra={"sensitivity": sensitivity})
            return [None] * len(texts)

        try:
            vectors = await self._provider.embed([redact(t) for t in texts])
        except Exception:
            # A dead embedding vendor must not break memory_save. Full-text
            # search still works and a backfill can populate the column later.
            logger.exception("embedding failed; continuing without vectors")
            return [None] * len(texts)
        return [v or None for v in vectors]

    async def aclose(self) -> None:
        await self._provider.aclose()
