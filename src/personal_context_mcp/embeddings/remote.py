"""OpenAI- and Gemini-backed embedding providers."""

from __future__ import annotations

import httpx

from personal_context_mcp.config import EMBEDDING_DIM
from personal_context_mcp.errors import PersonalContextError
from personal_context_mcp.logging_setup import get_logger

logger = get_logger(__name__)

#: Chars, not tokens - we only need a safe upper bound per input.
_MAX_INPUT_CHARS = 24_000


class OpenAIEmbeddingProvider:
    dimension = EMBEDDING_DIM

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            json={
                "model": self._model,
                "input": [t[:_MAX_INPUT_CHARS] for t in texts],
                "dimensions": self.dimension,
            },
        )
        if response.status_code != 200:
            raise PersonalContextError(f"Embedding request failed: HTTP {response.status_code}")
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in data]

    async def aclose(self) -> None:
        await self._client.aclose()


class GeminiEmbeddingProvider:
    dimension = EMBEDDING_DIM

    def __init__(self, api_key: str, model: str = "gemini-embedding-001") -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"x-goog-api-key": api_key},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            f"/models/{self._model}:batchEmbedContents",
            json={
                "requests": [
                    {
                        "model": f"models/{self._model}",
                        "content": {"parts": [{"text": t[:_MAX_INPUT_CHARS]}]},
                        "outputDimensionality": self.dimension,
                    }
                    for t in texts
                ]
            },
        )
        if response.status_code != 200:
            raise PersonalContextError(f"Embedding request failed: HTTP {response.status_code}")
        return [item["values"] for item in response.json()["embeddings"]]

    async def aclose(self) -> None:
        await self._client.aclose()
