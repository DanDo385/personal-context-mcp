"""Human review queue.

Where ambiguous routing goes instead of being guessed. Approving a review item
applies the suggested routing; reclassifying overrides it and records the
override on the originating classification event, which is how the quality of
the classifier can be measured against what a human actually wanted.

There is deliberately no generic delete: dismissing an item closes the
question without destroying the object it was about.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from personal_context.db.pool import Database
from personal_context.errors import NotFoundError


class ReviewService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_open(
        self, *, limit: int = 25, object_type: str | None = None
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT r.id, r.object_type, r.object_id, r.reason, r.question,
                   r.options, r.suggested, r.confidence, r.created_at,
                   e.classifier, e.model, e.degraded
            FROM review_queue r
            LEFT JOIN classification_events e ON e.id = r.classification_event_id
            WHERE r.status = 'open'
              AND ($1::text IS NULL OR r.object_type = $1)
            ORDER BY r.confidence NULLS FIRST, r.created_at
            LIMIT $2
            """,
            object_type,
            limit,
        )
        return [dict(r) for r in rows]

    async def get(self, review_id: UUID) -> dict[str, Any]:
        row = await self._db.fetchrow(
            """
            SELECT r.*, e.answers, e.classifier, e.model, e.input_hash, e.degraded
            FROM review_queue r
            LEFT JOIN classification_events e ON e.id = r.classification_event_id
            WHERE r.id = $1
            """,
            review_id,
        )
        if row is None:
            raise NotFoundError("Review item not found.")
        return dict(row)

    async def resolve(
        self,
        review_id: UUID,
        *,
        status: str,
        resolution: dict[str, Any] | None = None,
        overridden: bool = False,
        override_note: str | None = None,
    ) -> dict[str, Any]:
        item = await self.get(review_id)
        await self._db.execute(
            """
            UPDATE review_queue
            SET status = $2, resolution = $3, resolved_at = now()
            WHERE id = $1
            """,
            review_id,
            status,
            resolution or {},
        )
        if overridden and item.get("classification_event_id"):
            await self._db.execute(
                """
                UPDATE classification_events
                SET overridden_by_user = true, override_note = $2
                WHERE id = $1
                """,
                item["classification_event_id"],
                override_note,
            )
        return {
            "id": review_id,
            "status": status,
            "object_type": item["object_type"],
            "object_id": item["object_id"],
        }

    async def counts(self) -> dict[str, int]:
        rows = await self._db.fetch(
            """
            SELECT object_type, count(*) AS n FROM review_queue
            WHERE status = 'open' GROUP BY object_type
            """
        )
        return {r["object_type"]: int(r["n"]) for r in rows}
