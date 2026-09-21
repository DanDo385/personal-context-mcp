"""Classifier protocol and result types.

A classifier answers narrow, typed questions about state. It never writes
prose, never returns a path, and never decides what to do - Python maps its
answers onto actions through :mod:`personal_context.classifier.policy`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Answer:
    """One typed answer, normalised across the three primitives."""

    kind: str  # "noul" | "choice" | "score"
    value: Any  # float for noul, str for choice, float for score
    confidence: float | None = None  # Nouls carry none by design
    probabilities: dict[str, float] = field(default_factory=dict)
    legend: dict[str, Any] = field(default_factory=dict)

    @property
    def probability(self) -> float:
        """For a Noul, the probability of yes. For others, the winner's share."""
        if self.kind == "noul":
            return float(self.value)
        if self.probabilities:
            return max(self.probabilities.values())
        return float(self.confidence or 0.0)

    def as_json(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "value": self.value,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
        }


@dataclass(frozen=True)
class ClassificationResult:
    """Everything one classification produced, plus how it was produced."""

    answers: dict[str, Answer]
    classifier: str  # "jev" | "rules"
    model: str | None = None
    request_id: str | None = None
    questions_version: str = ""
    input_hash: str = ""
    input_tokens: int | None = None
    duration_ms: int | None = None
    #: True when the external classifier was unavailable or refused and the
    #: deterministic fallback produced this result. Anything derived from a
    #: degraded result is queued for reclassification.
    degraded: bool = False
    error: str | None = None

    def noul(self, key: str, default: float = 0.5) -> float:
        answer = self.answers.get(key)
        return float(answer.value) if answer and answer.kind == "noul" else default

    def choice(self, key: str) -> tuple[str | None, float]:
        answer = self.answers.get(key)
        if not answer or answer.kind != "choice":
            return None, 0.0
        return str(answer.value), float(answer.confidence or 0.0)

    def score(self, key: str, *, levels: int) -> tuple[float, float]:
        """Returns (normalised 0..1 score, confidence)."""
        answer = self.answers.get(key)
        if not answer or answer.kind != "score" or levels < 2:
            return 0.0, 0.0
        return float(answer.value) / (levels - 1), float(answer.confidence or 0.0)

    def answers_json(self) -> dict[str, Any]:
        return {key: answer.as_json() for key, answer in self.answers.items()}


@runtime_checkable
class Classifier(Protocol):
    name: str

    async def classify_conversation(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult: ...

    async def classify_checkpoint(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult: ...

    async def classify_artifact(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult: ...

    async def classify_note(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult: ...

    async def classify_list_candidate(
        self, state: dict[str, Any], list_candidates: dict[str, str]
    ) -> ClassificationResult: ...

    async def aclose(self) -> None: ...
