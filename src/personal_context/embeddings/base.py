"""Embedding provider abstraction.

Core code never imports a vendor SDK. Two rules are enforced here rather than
at the call sites, because a call site that forgets them leaks private text:

* content classified at a sensitivity level in ``PC_NO_EXTERNAL_SENSITIVITY``
  is never sent to an external API;
* text is truncated and optionally redacted before it leaves the process.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from personal_context.config import EMBEDDING_DIM

#: Patterns stripped before text leaves the process. Conservative on purpose:
#: over-redaction costs a little retrieval quality, under-redaction costs a
#: secret.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "[key]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[key]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.I), "[token]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[number]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
        "[private-key]",
    ),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


@runtime_checkable
class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class NullEmbeddingProvider:
    """No embeddings. Retrieval degrades to full-text search, which works.

    This is the default so the system is useful before any embedding vendor is
    configured, and so private content is never sent anywhere by accident.
    """

    dimension = EMBEDDING_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]

    async def aclose(self) -> None:
        return None
