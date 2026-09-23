"""Audit log.

Every AI-originated mutation lands here with enough context to answer:
who changed this, which model, why, and what did it look like before.

Writes are best-effort in the sense that an audit failure must not destroy the
user's data - but it *is* logged loudly, because a silent gap in the audit
trail is a real problem.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from personal_context_mcp.auth.principal import Principal, current_principal
from personal_context_mcp.db.pool import Database
from personal_context_mcp.logging_setup import get_logger, request_id_var

logger = get_logger(__name__)


class AuditService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        tool: str,
        action: str,
        object_type: str | None = None,
        object_id: UUID | None = None,
        principal: Principal | None = None,
        previous_version: dict[str, Any] | None = None,
        resulting_version: dict[str, Any] | None = None,
        classification_event_id: UUID | None = None,
        approval_status: str = "auto",
        provenance: str | None = None,
        conversation_ref: str | None = None,
        model: str | None = None,
        status: str = "ok",
        error: str | None = None,
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        principal = principal or current_principal()
        try:
            return await self._db.fetchval(
                """
                INSERT INTO audit_log (
                    request_id, principal, client_id, provider, model, tool, action,
                    object_type, object_id, conversation_ref, previous_version,
                    resulting_version, classification_event_id, approval_status,
                    provenance, status, error, duration_ms, metadata
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::pc_provenance,
                    $16,$17,$18,$19
                )
                RETURNING id
                """,
                request_id_var.get(),
                principal.subject,
                principal.client_id,
                principal.provider,
                model,
                tool,
                action,
                object_type,
                object_id,
                conversation_ref,
                previous_version,
                resulting_version,
                classification_event_id,
                approval_status,
                provenance,
                status,
                error,
                duration_ms,
                metadata or {},
            )
        except Exception:
            logger.exception("audit write failed", extra={"tool": tool, "action": action})
            return None

    async def history(
        self, *, object_type: str, object_id: UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT id, created_at, provider, principal, tool, action, model,
                   approval_status, classification_event_id, metadata
            FROM audit_log
            WHERE object_type = $1 AND object_id = $2
            ORDER BY created_at DESC
            LIMIT $3
            """,
            object_type,
            object_id,
            limit,
        )
        return [dict(r) for r in rows]


async def audit_write(db: Database, **kwargs: Any) -> UUID | None:
    """Convenience wrapper for one-off audit rows."""
    return await AuditService(db).record(**kwargs)
