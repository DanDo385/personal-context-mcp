"""Durable memory (layer 3), with deduplication that actually decides.

Before any memory is written we run four checks in increasing cost order:

1. exact content hash                    -> duplicate
2. same subject + very similar text      -> update or supersede
3. semantic similarity (when embeddings
   are configured)                       -> supersede or duplicate
4. otherwise                             -> new

Nothing is destructively overwritten. Superseding writes a revision row and
links the old memory to the new one, so provenance survives.
"""

from __future__ import annotations

import difflib
from typing import Any
from uuid import UUID

import asyncpg

from personal_context.config import Settings
from personal_context.db.pool import Database
from personal_context.embeddings.service import EmbeddingService
from personal_context.models import MemoryRecord, MemorySaveResult, Provenance_
from personal_context.util import normalized_sha256, sha256_text

#: Ratio above which two memories about the same subject are "the same claim
#: restated". Below it they are treated as distinct facts.
_TEXT_SIMILARITY = 0.88


def _provenance(row: asyncpg.Record) -> Provenance_:
    return Provenance_(
        kind=row["provenance"],
        provider=row.get("source_provider"),
        conversation_id=row.get("source_conversation_id"),
        recorded_at=row.get("created_at"),
        confidence=row.get("confidence"),
    )


def _record(row: asyncpg.Record) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        content=row["content"],
        kind=row["kind"],
        subject=row["subject"],
        project=row.get("project_slug"),
        importance=row["importance"],
        status=row["status"],
        provenance=_provenance(row),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MemoryService:
    def __init__(self, db: Database, embeddings: EmbeddingService, settings: Settings) -> None:
        self._db = db
        self._embeddings = embeddings
        self._settings = settings

    # ---------------------------------------------------------------- write

    async def save(
        self,
        *,
        content: str,
        kind: str = "fact",
        subject: str | None = None,
        project_id: UUID | None = None,
        provenance: str = "user_statement",
        sensitivity: str = "normal",
        importance: float = 0.5,
        source_provider: str | None = None,
        source_conversation_id: UUID | None = None,
        confidence: float | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> MemorySaveResult:
        content = content.strip()
        if not content:
            raise ValueError("Memory content is empty.")

        executor = conn or self._db
        content_hash = sha256_text(content)
        dedup_hash = normalized_sha256(content)

        # 1. Exact duplicate.
        existing = await executor.fetchrow(
            """
            SELECT id, status FROM memories
            WHERE content_sha256 = $1 AND coalesce(subject,'') = coalesce($2,'')
            """,
            content_hash,
            subject,
        )
        if existing:
            await executor.execute(
                "UPDATE memories SET last_used_at = now(), updated_at = now() WHERE id = $1",
                existing["id"],
            )
            return MemorySaveResult(
                id=existing["id"],
                outcome="duplicate",
                reason="An identical memory already exists; its timestamp was refreshed.",
            )

        # 2/3. Near duplicate, by subject then by embedding.
        candidate = await self._find_near_duplicate(
            executor,
            content=content,
            subject=subject,
            dedup_hash=dedup_hash,
            sensitivity=sensitivity,
        )

        embedding = await self._embeddings.embed_one(content, sensitivity=sensitivity)

        if candidate is not None:
            return await self._supersede(
                executor,
                previous=candidate,
                content=content,
                content_hash=content_hash,
                kind=kind,
                subject=subject,
                project_id=project_id,
                provenance=provenance,
                sensitivity=sensitivity,
                importance=importance,
                source_provider=source_provider,
                source_conversation_id=source_conversation_id,
                confidence=confidence,
                embedding=embedding,
            )

        memory_id = await executor.fetchval(
            """
            INSERT INTO memories (
                content, content_sha256, kind, subject, project_id, source_provider,
                source_conversation_id, provenance, sensitivity, importance,
                confidence, embedding
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::pc_provenance,$9::pc_sensitivity,
                      $10,$11,$12)
            ON CONFLICT (content_sha256, coalesce(subject, '')) DO NOTHING
            RETURNING id
            """,
            content,
            content_hash,
            kind,
            subject,
            project_id,
            source_provider,
            source_conversation_id,
            provenance,
            sensitivity,
            importance,
            confidence,
            embedding,
        )
        if memory_id is None:  # lost a race with a concurrent identical insert
            memory_id = await executor.fetchval(
                """
                SELECT id FROM memories
                WHERE content_sha256 = $1 AND coalesce(subject,'') = coalesce($2,'')
                """,
                content_hash,
                subject,
            )
            return MemorySaveResult(
                id=memory_id, outcome="duplicate", reason="Concurrent identical save."
            )

        await executor.execute(
            """
            INSERT INTO memory_revisions (memory_id, version, content, provenance,
                                          changed_by_provider)
            VALUES ($1, 1, $2, $3::pc_provenance, $4)
            """,
            memory_id,
            content,
            provenance,
            source_provider,
        )
        return MemorySaveResult(id=memory_id, outcome="created", reason="New durable memory.")

    async def _find_near_duplicate(
        self,
        executor: Any,
        *,
        content: str,
        subject: str | None,
        dedup_hash: str,
        sensitivity: str,
    ) -> asyncpg.Record | None:
        # Normalised-text match: same claim, different punctuation or case.
        if subject:
            rows = await executor.fetch(
                """
                SELECT id, content, version_of.version AS version
                FROM memories
                LEFT JOIN LATERAL (
                    SELECT max(version) AS version FROM memory_revisions r
                    WHERE r.memory_id = memories.id
                ) version_of ON true
                WHERE status = 'active' AND subject = $1
                ORDER BY updated_at DESC
                LIMIT 25
                """,
                subject,
            )
            for row in rows:
                if normalized_sha256(row["content"]) == dedup_hash:
                    return row
                ratio = difflib.SequenceMatcher(None, row["content"], content).ratio()
                if ratio >= _TEXT_SIMILARITY:
                    return row

        # Semantic match, only when embeddings are actually available and the
        # content is allowed to be embedded.
        if not self._embeddings.enabled or not self._embeddings.allowed(sensitivity):
            return None
        vector = await self._embeddings.embed_one(content, sensitivity=sensitivity)
        if not vector:
            return None
        return await executor.fetchrow(
            """
            SELECT m.id, m.content,
                   (SELECT max(version) FROM memory_revisions r WHERE r.memory_id = m.id)
                       AS version
            FROM memories m
            WHERE m.status = 'active' AND m.embedding IS NOT NULL
              AND (m.embedding <=> $1) < $2
            ORDER BY m.embedding <=> $1
            LIMIT 1
            """,
            vector,
            self._settings.memory_dedup_distance,
        )

    async def _supersede(
        self,
        executor: Any,
        *,
        previous: asyncpg.Record,
        content: str,
        content_hash: str,
        kind: str,
        subject: str | None,
        project_id: UUID | None,
        provenance: str,
        sensitivity: str,
        importance: float,
        source_provider: str | None,
        source_conversation_id: UUID | None,
        confidence: float | None,
        embedding: list[float] | None,
    ) -> MemorySaveResult:
        """Replace a memory's content in place, keeping the old text as a revision.

        Updating in place rather than inserting a sibling is what stops the
        table filling with near-identical restatements of one fact; the
        revision row is what stops that from losing history.
        """
        next_version = int(previous["version"] or 1) + 1
        await executor.execute(
            """
            INSERT INTO memory_revisions (memory_id, version, content, provenance,
                                          changed_by_provider)
            VALUES ($1,$2,$3,$4::pc_provenance,$5)
            ON CONFLICT (memory_id, version) DO NOTHING
            """,
            previous["id"],
            next_version,
            content,
            provenance,
            source_provider,
        )
        await executor.execute(
            """
            UPDATE memories
            SET content = $2,
                content_sha256 = $3,
                kind = $4,
                subject = coalesce($5, subject),
                project_id = coalesce($6, project_id),
                provenance = $7::pc_provenance,
                sensitivity = $8::pc_sensitivity,
                importance = greatest(importance, $9),
                confidence = coalesce($10, confidence),
                source_provider = coalesce($11, source_provider),
                source_conversation_id = coalesce($12, source_conversation_id),
                embedding = coalesce($13, embedding),
                updated_at = now(),
                last_used_at = now()
            WHERE id = $1
            """,
            previous["id"],
            content,
            content_hash,
            kind,
            subject,
            project_id,
            provenance,
            sensitivity,
            importance,
            confidence,
            source_provider,
            source_conversation_id,
            embedding,
        )
        return MemorySaveResult(
            id=previous["id"],
            outcome="updated",
            reason=(
                "An existing memory said substantially the same thing; it was "
                "updated to this wording and the previous text kept as revision "
                f"{next_version - 1}."
            ),
        )

    async def retract(self, memory_id: UUID, *, reason: str) -> None:
        await self._db.execute(
            """
            UPDATE memories
            SET status = 'retracted',
                metadata = metadata || jsonb_build_object('retraction_reason', $2::text)
            WHERE id = $1
            """,
            memory_id,
            reason,
        )

    # ----------------------------------------------------------------- read

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: UUID | None = None,
        kind: str | None = None,
        min_importance: float = 0.0,
    ) -> list[MemoryRecord]:
        vector = await self._embeddings.embed_one(query) if self._embeddings.enabled else None
        rows = await self._db.fetch(
            """
            WITH text_hits AS (
                SELECT m.id,
                       ts_rank_cd(m.search_tsv, websearch_to_tsquery('english', $1)) AS rank
                FROM memories m
                WHERE m.search_tsv @@ websearch_to_tsquery('english', $1)
            ),
            vector_hits AS (
                SELECT m.id, 1 - (m.embedding <=> $5) AS rank
                FROM memories m
                WHERE $5::vector IS NOT NULL AND m.embedding IS NOT NULL
                ORDER BY m.embedding <=> $5
                LIMIT 50
            ),
            merged AS (
                SELECT id, max(rank) AS rank FROM (
                    SELECT id, rank FROM text_hits
                    UNION ALL
                    SELECT id, rank FROM vector_hits
                ) u GROUP BY id
            )
            SELECT m.*, p.slug AS project_slug,
                   merged.rank
                     * (0.6 + 0.4 * m.importance)
                     * (1.0 / (1.0 + extract(epoch FROM now() - m.updated_at) / 7776000.0))
                     AS score
            FROM merged
            JOIN memories m ON m.id = merged.id
            LEFT JOIN projects p ON p.id = m.project_id
            WHERE m.status = 'active'
              AND ($2::uuid IS NULL OR m.project_id = $2)
              AND ($3::text IS NULL OR m.kind = $3)
              AND m.importance >= $4
            ORDER BY score DESC
            LIMIT $6
            """,
            query,
            project_id,
            kind,
            min_importance,
            vector,
            limit,
        )
        if rows:
            await self._mark_used([r["id"] for r in rows])
        return [_record(r) for r in rows]

    async def recent(
        self, *, limit: int = 20, project_id: UUID | None = None
    ) -> list[MemoryRecord]:
        rows = await self._db.fetch(
            """
            SELECT m.*, p.slug AS project_slug
            FROM memories m
            LEFT JOIN projects p ON p.id = m.project_id
            WHERE m.status = 'active'
              AND ($1::uuid IS NULL OR m.project_id = $1)
            ORDER BY m.importance DESC, m.updated_at DESC
            LIMIT $2
            """,
            project_id,
            limit,
        )
        return [_record(r) for r in rows]

    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        row = await self._db.fetchrow(
            """
            SELECT m.*, p.slug AS project_slug
            FROM memories m LEFT JOIN projects p ON p.id = m.project_id
            WHERE m.id = $1
            """,
            memory_id,
        )
        return _record(row) if row else None

    async def history(self, memory_id: UUID) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT version, content, provenance, changed_by_provider, created_at
            FROM memory_revisions WHERE memory_id = $1 ORDER BY version DESC
            """,
            memory_id,
        )
        return [dict(r) for r in rows]

    async def _mark_used(self, ids: list[UUID]) -> None:
        await self._db.execute(
            """
            UPDATE memories SET last_used_at = now(), use_count = use_count + 1
            WHERE id = ANY($1::uuid[])
            """,
            ids,
        )
