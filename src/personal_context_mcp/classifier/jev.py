"""Jev classifier, using the official ``typesafe-sdk``.

One request per object. Every independent question about the same state goes
in that single request: Jev ingests the state once and evaluates the questions
in parallel, which the TypeSafe docs measure at roughly an order of magnitude
cheaper and faster than issuing them separately.

Failure is never fatal. If Jev is unreachable, rate-limits us, or returns
something we cannot parse, we fall back to
:class:`~personal_context_mcp.classifier.rules.RulesClassifier` with
``degraded=True`` so the caller knows to queue a reclassification.
"""

from __future__ import annotations

import json
import time
from typing import Any

from typesafe_sdk import (
    AsyncTypeSafeClient,
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeError,
)

from personal_context_mcp.classifier.base import Answer, ClassificationResult
from personal_context_mcp.classifier.questions import (
    QUESTIONS_VERSION,
    artifact_questions,
    checkpoint_questions,
    list_candidate_questions,
    note_questions,
)
from personal_context_mcp.classifier.rules import RulesClassifier
from personal_context_mcp.config import Settings
from personal_context_mcp.embeddings.base import redact
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.util import sha256_text, truncate_for_budget

logger = get_logger(__name__)


def _normalise_answers(response: Any) -> dict[str, Answer]:
    """Map a SystemOneResponse onto our transport-independent Answer type."""
    out: dict[str, Answer] = {}
    for key, answer in (response.answers or {}).items():
        kind = getattr(answer, "type", None)
        if kind == "noul":
            out[key] = Answer("noul", float(answer.noul))
        elif kind == "choice":
            out[key] = Answer(
                "choice",
                str(answer.choice),
                confidence=float(answer.confidence),
                probabilities={k: float(v) for k, v in (answer.probabilities or {}).items()},
            )
        elif kind == "score":
            out[key] = Answer(
                "score",
                float(answer.score),
                confidence=float(answer.confidence),
                probabilities={str(k): float(v) for k, v in (answer.probabilities or {}).items()},
                legend=dict(answer.legend or {}),
            )
    return out


class JevClassifier:
    """Calls Jev; degrades to rules on any failure."""

    name = "jev"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fallback = RulesClassifier(degraded=True, error="jev_unavailable")
        self._blocked_sensitivity = set(settings.no_external_sensitivity)
        self._client = AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key.get_secret_value(),
            model=settings.jev_model,
            timeout=settings.jev_timeout_seconds,
            retry=RetryPolicy(max_retries=settings.jev_max_retries),
        )

    # ------------------------------------------------------------ internals

    def _prepare_state(self, state: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Redact, bound, and hash the state we are about to send.

        Jev takes 64k tokens per request (32k for state plus the longest
        question). We budget in characters, conservatively, and trim the
        longest free-text fields first so structured fields survive.
        """
        prepared: dict[str, Any] = {}
        budget = self._settings.jev_max_state_chars
        # Structured/short fields first so trimming hits the transcript, not
        # the project list.
        for key, value in sorted(state.items(), key=lambda kv: len(str(kv[1]))):
            if key == "input_hash":
                continue
            if isinstance(value, str):
                text = redact(value)
                if len(text) > budget:
                    text = truncate_for_budget(text, max(200, int(budget / 3.6)))
                budget -= len(text)
                prepared[key] = text
            else:
                prepared[key] = value
                budget -= len(json.dumps(value, default=str))
            if budget <= 0:
                budget = 500  # keep a floor so later small fields still fit

        input_hash = sha256_text(json.dumps(prepared, sort_keys=True, default=str))
        return prepared, input_hash

    def _blocked(self, state: dict[str, Any]) -> bool:
        return str(state.get("sensitivity", "normal")) in self._blocked_sensitivity

    async def _ask(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        fallback: Any,
        candidates: dict[str, str],
    ) -> ClassificationResult:
        # Privacy routing happens BEFORE any external call, not after.
        if self._blocked(state):
            logger.info(
                "classification kept local by privacy policy",
                extra={"sensitivity": state.get("sensitivity")},
            )
            result = await fallback(state, candidates)
            return ClassificationResult(
                **{**result.__dict__, "degraded": False, "error": "blocked_by_privacy_policy"}
            )

        prepared, input_hash = self._prepare_state(state)
        started = time.perf_counter()
        try:
            response = await self._client.system_one(state=prepared, questions=questions)
        except (TypeSafeAPIError, TypeSafeAPIConnectionError, TypeSafeError) as exc:
            logger.warning(
                "jev unavailable; using deterministic fallback",
                extra={"error_type": type(exc).__name__},
            )
            result = await fallback(state, candidates)
            return ClassificationResult(
                **{**result.__dict__, "input_hash": input_hash, "error": type(exc).__name__}
            )
        except Exception as exc:
            logger.exception("unexpected classifier failure; using deterministic fallback")
            result = await fallback(state, candidates)
            return ClassificationResult(
                **{**result.__dict__, "input_hash": input_hash, "error": type(exc).__name__}
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        answers = _normalise_answers(response)
        if not answers:
            logger.warning("jev returned no usable answers; using deterministic fallback")
            result = await fallback(state, candidates)
            return ClassificationResult(
                **{**result.__dict__, "input_hash": input_hash, "error": "empty_answers"}
            )

        return ClassificationResult(
            answers=answers,
            classifier=self.name,
            model=response.model,
            request_id=getattr(response, "request_id", None),
            questions_version=QUESTIONS_VERSION,
            input_hash=input_hash,
            input_tokens=getattr(response.usage, "input_tokens", None),
            duration_ms=duration_ms,
            degraded=False,
        )

    # ----------------------------------------------------------- public API

    async def classify_conversation(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        return await self.classify_checkpoint(state, project_candidates)

    async def classify_checkpoint(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        return await self._ask(
            state,
            checkpoint_questions(project_candidates),
            fallback=self._fallback.classify_checkpoint,
            candidates=project_candidates,
        )

    async def classify_artifact(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        return await self._ask(
            state,
            artifact_questions(project_candidates),
            fallback=self._fallback.classify_artifact,
            candidates=project_candidates,
        )

    async def classify_note(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        return await self._ask(
            state,
            note_questions(project_candidates),
            fallback=self._fallback.classify_note,
            candidates=project_candidates,
        )

    async def classify_list_candidate(
        self, state: dict[str, Any], list_candidates: dict[str, str]
    ) -> ClassificationResult:
        return await self._ask(
            state,
            list_candidate_questions(list_candidates),
            fallback=self._fallback.classify_list_candidate,
            candidates=list_candidates,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_classifier(settings: Settings):
    if settings.classifier == "jev" and settings.typesafe_api_key.get_secret_value():
        logger.info("classifier: jev", extra={"jev_model": settings.jev_model})
        return JevClassifier(settings)
    logger.info("classifier: rules (deterministic only)")
    return RulesClassifier()
