"""The sync job queue.

The cloud service only ever *enqueues*. It has no route into the Mac and no
credential for it. The local worker polls, leases, writes, and acknowledges.

Concurrency model: one open job per vault path (enforced by a partial unique
index), leased with a deadline so a worker that dies does not wedge the queue,
and guarded by ``expected_sha256`` so a file edited by hand since the job was
created produces a conflict instead of a silent overwrite.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from personal_context_mcp.db.pool import Database
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.obsidian.paths import validate_vault_path

logger = get_logger(__name__)

LEASE_SECONDS = 300


#: Where the hash of the file last written for an object is kept.
_SYNCED_HASH_SQL: dict[str, str] = {
    "note": "SELECT vault_synced_sha256 FROM notes WHERE id = $1",
    "moc": "SELECT rendered_sha256 FROM mocs WHERE id = $1",
}


class SyncQueue:
    def __init__(self, db: Database, allowed_roots: dict[str, str]) -> None:
        self._db = db
        self._roots = allowed_roots

    async def enqueue(
        self,
        *,
        job_type: str,
        object_type: str,
        object_id: UUID,
        vault_path: str,
        payload: dict[str, Any],
        content_sha256: str | None = None,
        expected_sha256: str | None = None,
        requires_approval: bool = False,
        source_provider: str | None = None,
        classification_event_id: UUID | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> UUID | None:
        """Queue a vault write. Validates the path before it is ever stored.

        For notes and maps, a write that does not say what it expects to
        replace is given the hash of the file the worker last wrote. The
        worker refuses to overwrite a file whose hash differs, so a human edit
        it has not yet seen - say one made while it was stopped - comes back
        as a conflict instead of being overwritten.
        """
        vault_path = validate_vault_path(vault_path, self._roots)
        executor = conn or self._db
        if expected_sha256 is None and object_type in _SYNCED_HASH_SQL:
            expected_sha256 = await executor.fetchval(_SYNCED_HASH_SQL[object_type], object_id)

        # Supersede any open job for the same path: the newest content wins
        # and the queue stays one-deep per file.
        await executor.execute(
            """
            UPDATE sync_jobs SET status = 'cancelled', completed_at = now()
            WHERE vault_path = $1 AND status IN ('queued', 'approved')
            """,
            vault_path,
        )
        job_id = await executor.fetchval(
            """
            INSERT INTO sync_jobs (
                job_type, object_type, object_id, vault_path, payload,
                content_sha256, expected_sha256, requires_approval,
                status, source_provider, classification_event_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,
                      CASE WHEN $8 THEN 'queued' ELSE 'approved' END,
                      $9,$10)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            job_type,
            object_type,
            object_id,
            vault_path,
            payload,
            content_sha256,
            expected_sha256,
            requires_approval,
            source_provider,
            classification_event_id,
        )
        if job_id:
            logger.info(
                "queued vault write",
                extra={"job_type": job_type, "vault_path": vault_path},
            )
        return job_id

    async def lease(self, *, worker_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Atomically claim up to `limit` ready jobs."""
        rows = await self._db.fetch(
            """
            WITH ready AS (
                SELECT id FROM sync_jobs
                WHERE status = 'approved'
                   OR (status = 'leased' AND leased_until < now())
                ORDER BY created_at
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            UPDATE sync_jobs j
            SET status = 'leased',
                lease_owner = $1,
                leased_until = now() + make_interval(secs => $3),
                attempts = j.attempts + 1
            FROM ready
            WHERE j.id = ready.id
            RETURNING j.id, j.job_type, j.object_type, j.object_id, j.vault_path,
                      j.payload, j.content_sha256, j.expected_sha256, j.attempts
            """,
            worker_id,
            limit,
            float(LEASE_SECONDS),
        )
        return [dict(r) for r in rows]

    async def complete(self, job_id: UUID, *, worker_id: str, written_sha256: str | None) -> None:
        job = await self._db.fetchrow(
            """
            UPDATE sync_jobs
            SET status = 'done', completed_at = now(), last_error = NULL
            WHERE id = $1 AND lease_owner = $2
            RETURNING object_type, object_id
            """,
            job_id,
            worker_id,
        )
        if job is not None and written_sha256:
            # Remember exactly what is on disk, so the next write of this file
            # can say what it expects to replace and a human edit made in
            # between comes back as a conflict instead of being overwritten.
            if job["object_type"] == "note":
                await self._db.execute(
                    "UPDATE notes SET vault_synced_sha256 = $2, vault_synced_at = now() "
                    "WHERE id = $1",
                    job["object_id"],
                    written_sha256,
                )
            elif job["object_type"] == "moc":
                await self._db.execute(
                    "UPDATE mocs SET rendered_sha256 = $2 WHERE id = $1",
                    job["object_id"],
                    written_sha256,
                )
        await self._event(
            job_id,
            direction="outbound",
            event_type="acked",
            sha256=written_sha256,
            worker_id=worker_id,
        )

    async def fail(
        self, job_id: UUID, *, worker_id: str, error: str, conflict: bool = False
    ) -> None:
        # A conflict is terminal until a human or a fresh job resolves it;
        # ordinary failures go back to the queue for another attempt.
        await self._db.execute(
            """
            UPDATE sync_jobs
            SET status = CASE
                    WHEN $3 THEN 'conflict'
                    WHEN attempts >= 5 THEN 'failed'
                    ELSE 'approved' END,
                last_error = $4,
                lease_owner = NULL,
                leased_until = NULL
            WHERE id = $1 AND lease_owner = $2
            """,
            job_id,
            worker_id,
            conflict,
            error[:1000],
        )
        await self._event(
            job_id,
            direction="outbound",
            event_type="conflict" if conflict else "failed",
            worker_id=worker_id,
            detail=error[:1000],
        )

    async def _event(
        self,
        job_id: UUID | None,
        *,
        direction: str,
        event_type: str,
        vault_path: str | None = None,
        sha256: str | None = None,
        worker_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO sync_events (sync_job_id, direction, event_type, vault_path,
                                     sha256, worker_id, detail)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            job_id,
            direction,
            event_type,
            vault_path,
            sha256,
            worker_id,
            detail,
        )

    async def record_inbound(
        self, *, vault_path: str, sha256: str, worker_id: str, detail: str
    ) -> None:
        """Log a human edit picked up from the vault."""
        await self._event(
            None,
            direction="inbound",
            event_type="human_edit",
            vault_path=vault_path,
            sha256=sha256,
            worker_id=worker_id,
            detail=detail,
        )

    async def pending_count(self) -> int:
        return int(
            await self._db.fetchval(
                "SELECT count(*) FROM sync_jobs WHERE status IN ('queued','approved','leased')"
            )
        )

    async def approve(self, job_id: UUID) -> bool:
        result = await self._db.execute(
            "UPDATE sync_jobs SET status = 'approved' WHERE id = $1 AND status = 'queued'",
            job_id,
        )
        return result.rsplit(" ", 1)[-1] != "0"
