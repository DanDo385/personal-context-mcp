"""Classification orchestration and its event log.

Wraps the classifier + policy engine so that every routing decision, from Jev
or from the deterministic fallback, is persisted to ``classification_events``
before anything acts on it. That table is what makes it possible to ask later
whether the classifications were any good.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from personal_context.classifier.base import ClassificationResult, Classifier
from personal_context.classifier.policy import PolicyEngine, Routing
from personal_context.config import Settings
from personal_context.db.pool import Database
from personal_context.logging_setup import get_logger

logger = get_logger(__name__)


class ClassificationService:
    def __init__(self, db: Database, classifier: Classifier, settings: Settings) -> None:
        self._db = db
        self._classifier = classifier
        self._settings = settings

    def policy(self, *, historical_import: bool = False) -> PolicyEngine:
        return PolicyEngine(self._settings, historical_import=historical_import)

    async def project_candidates(self, *, limit: int | None = None) -> dict[str, str]:
        """Active projects, most recently touched first, as Choice criteria.

        Bounded on purpose: handing Jev an unbounded list of every project
        ever created makes the Choice worse. When the cap bites, the oldest
        inactive projects drop off and the object lands in review rather than
        being assigned to a project that was never offered.
        """
        limit = limit or self._settings.jev_max_project_candidates
        rows = await self._db.fetch(
            """
            SELECT slug, name, coalesce(description, goals, '') AS blurb
            FROM projects
            WHERE status IN ('active', 'paused')
            ORDER BY last_active_at DESC
            LIMIT $1
            """,
            limit,
        )
        return {
            row["slug"]: (f"{row['name']}. {row['blurb']}".strip()[:300] or row["name"])
            for row in rows
        }

    async def list_candidates(self, *, limit: int = 40) -> dict[str, str]:
        rows = await self._db.fetch(
            """
            SELECT slug, name, coalesce(description, '') AS blurb, list_type
            FROM lists
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT $1
            """,
            limit,
        )
        return {
            row["slug"]: (f"{row['name']} ({row['list_type']}). {row['blurb']}".strip()[:240])
            for row in rows
        }

    # ------------------------------------------------------------ recording

    async def record(
        self,
        result: ClassificationResult,
        routing: Routing,
        *,
        object_type: str,
        object_id: UUID | None,
    ) -> UUID | None:
        try:
            return await self._db.fetchval(
                """
                INSERT INTO classification_events (
                    object_type, object_id, classifier, model, request_id,
                    questions_version, input_hash, answers, confidence,
                    action_taken, degraded, input_tokens, duration_ms, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                RETURNING id
                """,
                object_type,
                object_id,
                result.classifier,
                result.model,
                result.request_id,
                result.questions_version,
                result.input_hash,
                result.answers_json(),
                routing.confidence,
                routing.action_taken,
                result.degraded,
                result.input_tokens,
                result.duration_ms,
                {"routing": routing.as_json(), "error": result.error},
            )
        except Exception:
            logger.exception("failed to persist classification event")
            return None

    async def queue_reclassification(
        self, *, object_type: str, object_id: UUID, reason: str
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO reclassification_queue (object_type, object_id, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (object_type, object_id) DO UPDATE
                SET reason = EXCLUDED.reason,
                    attempts = reclassification_queue.attempts + 1,
                    next_attempt_at = now() + make_interval(
                        mins => least(60, power(2, reclassification_queue.attempts + 1)::int)
                    ),
                    updated_at = now()
            """,
            object_type,
            object_id,
            reason,
        )

    async def queue_review(
        self,
        *,
        object_type: str,
        object_id: UUID,
        reason: str,
        question: str,
        options: list[dict[str, Any]] | None = None,
        suggested: dict[str, Any] | None = None,
        confidence: float | None = None,
        classification_event_id: UUID | None = None,
    ) -> UUID | None:
        return await self._db.fetchval(
            """
            INSERT INTO review_queue (
                object_type, object_id, reason, question, options, suggested,
                confidence, classification_event_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (object_type, object_id, reason) WHERE status = 'open'
                DO UPDATE SET question = EXCLUDED.question,
                              options = EXCLUDED.options,
                              suggested = EXCLUDED.suggested,
                              confidence = EXCLUDED.confidence,
                              updated_at = now()
            RETURNING id
            """,
            object_type,
            object_id,
            reason,
            question,
            options or [],
            suggested,
            confidence,
            classification_event_id,
        )

    # --------------------------------------------------------- classify + act

    async def classify_checkpoint(
        self,
        state: dict[str, Any],
        *,
        object_id: UUID | None = None,
        historical_import: bool = False,
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        candidates = await self.project_candidates()
        result = await self._classifier.classify_checkpoint(state, candidates)
        routing = self.policy(historical_import=historical_import).route(result)
        event_id = await self.record(result, routing, object_type="checkpoint", object_id=object_id)
        if result.degraded and object_id is not None:
            await self.queue_reclassification(
                object_type="checkpoint", object_id=object_id, reason="classifier_degraded"
            )
        return result, routing, event_id

    async def classify_artifact(
        self,
        state: dict[str, Any],
        *,
        object_id: UUID | None = None,
        historical_import: bool = False,
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        candidates = await self.project_candidates()
        result = await self._classifier.classify_artifact(state, candidates)
        routing = self.policy(historical_import=historical_import).route(result)
        event_id = await self.record(result, routing, object_type="artifact", object_id=object_id)
        if result.degraded and object_id is not None:
            await self.queue_reclassification(
                object_type="artifact", object_id=object_id, reason="classifier_degraded"
            )
        return result, routing, event_id

    async def classify_note(
        self, state: dict[str, Any], *, object_id: UUID | None = None
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        candidates = await self.project_candidates()
        result = await self._classifier.classify_note(state, candidates)
        routing = self.policy().route(result)
        event_id = await self.record(result, routing, object_type="note", object_id=object_id)
        return result, routing, event_id

    async def classify_list_candidate(
        self, state: dict[str, Any]
    ) -> tuple[ClassificationResult, dict[str, str]]:
        candidates = await self.list_candidates()
        result = await self._classifier.classify_list_candidate(state, candidates)
        return result, candidates
