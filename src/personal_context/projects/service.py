"""Projects: the central graph node.

A project ties together conversations, memories, decisions, tasks, lists,
notes and artifacts. Slugs are generated from the name by code so that
"Personal AI", "personal-ai" and "personal ai" resolve to one project.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from personal_context.db.pool import Database
from personal_context.errors import NotFoundError
from personal_context.models import ProjectRecord
from personal_context.util import sha256_text, slugify


def _record(row: asyncpg.Record) -> ProjectRecord:
    return ProjectRecord(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        description=row["description"],
        status=row["status"],
        goals=row["goals"],
        last_active_at=row["last_active_at"],
        open_task_count=row.get("open_task_count", 0) or 0,
        decision_count=row.get("decision_count", 0) or 0,
    )


class ProjectService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def resolve(self, reference: str | None) -> UUID | None:
        """Resolve a slug, name, or UUID string to a project id.

        Returns None for an unknown project rather than creating one: a typo
        in a tool argument must not silently spawn a duplicate project.
        """
        if not reference:
            return None
        reference = reference.strip()
        if not reference:
            return None

        row = await self._db.fetchrow(
            """
            SELECT id FROM projects
            WHERE slug = $1
               OR lower(name) = lower($2)
               OR ($3::uuid IS NOT NULL AND id = $3::uuid)
            ORDER BY (slug = $1) DESC
            LIMIT 1
            """,
            slugify(reference),
            reference,
            reference if _looks_like_uuid(reference) else None,
        )
        return row["id"] if row else None

    async def require(self, reference: str) -> UUID:
        project_id = await self.resolve(reference)
        if project_id is None:
            raise NotFoundError(
                f"No project matches {reference!r}. Call project_list to see the "
                "existing projects, or project_create to start a new one."
            )
        return project_id

    async def get(self, reference: str) -> ProjectRecord:
        project_id = await self.require(reference)
        row = await self._db.fetchrow(
            """
            SELECT p.id, p.slug, p.name, p.description, p.status, p.goals, p.last_active_at,
                   (SELECT count(*) FROM tasks t
                     WHERE t.project_id = p.id AND t.status = 'open') AS open_task_count,
                   (SELECT count(*) FROM decisions d
                     WHERE d.project_id = p.id AND d.status = 'active') AS decision_count
            FROM projects p WHERE p.id = $1
            """,
            project_id,
        )
        if row is None:  # pragma: no cover - require() already proved it exists
            raise NotFoundError("Project disappeared between lookup and read.")
        return _record(row)

    async def list_all(
        self, *, status: str | None = "active", limit: int = 50
    ) -> list[ProjectRecord]:
        rows = await self._db.fetch(
            """
            SELECT p.id, p.slug, p.name, p.description, p.status, p.goals, p.last_active_at,
                   (SELECT count(*) FROM tasks t
                     WHERE t.project_id = p.id AND t.status = 'open') AS open_task_count,
                   (SELECT count(*) FROM decisions d
                     WHERE d.project_id = p.id AND d.status = 'active') AS decision_count
            FROM projects p
            WHERE ($1::text IS NULL OR p.status = $1)
            ORDER BY p.last_active_at DESC
            LIMIT $2
            """,
            status,
            limit,
        )
        return [_record(r) for r in rows]

    async def search(self, query: str, *, limit: int = 10) -> list[ProjectRecord]:
        rows = await self._db.fetch(
            """
            SELECT p.id, p.slug, p.name, p.description, p.status, p.goals, p.last_active_at,
                   0 AS open_task_count, 0 AS decision_count,
                   CASE
                     WHEN lower(p.name) = lower($1) THEN 3
                     WHEN p.name ILIKE $1 || '%' THEN 2
                     ELSE 1
                   END AS name_rank
            FROM projects p
            WHERE p.name ILIKE '%' || $1 || '%'
               OR p.slug ILIKE '%' || $1 || '%'
               OR coalesce(p.description, '') ILIKE '%' || $1 || '%'
            ORDER BY name_rank DESC, p.last_active_at DESC
            LIMIT $2
            """,
            query,
            limit,
        )
        return [_record(r) for r in rows]

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        goals: str | None = None,
        status: str = "active",
        source_provider: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool]:
        """Create a project, or return the existing one with the same slug."""
        slug = slugify(name)
        executor = conn or self._db
        row = await executor.fetchrow(
            """
            INSERT INTO projects (slug, name, description, goals, status, source_provider)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (slug) DO UPDATE
                SET last_active_at = now(),
                    description = coalesce(projects.description, EXCLUDED.description),
                    goals = coalesce(projects.goals, EXCLUDED.goals)
            RETURNING id, (xmax = 0) AS created
            """,
            slug,
            name.strip(),
            description,
            goals,
            status,
            source_provider,
        )
        if row is None:  # pragma: no cover - the upsert always returns a row
            raise RuntimeError("project upsert returned no row")
        return row["id"], bool(row["created"])

    async def update(
        self,
        reference: str,
        *,
        status: str | None = None,
        goals: str | None = None,
        description: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        project_id = await self.require(reference)
        executor = conn or self._db
        before = await executor.fetchrow(
            "SELECT status, goals, description FROM projects WHERE id = $1", project_id
        )
        await executor.execute(
            """
            UPDATE projects
            SET status = coalesce($2, status),
                goals = coalesce($3, goals),
                description = coalesce($4, description),
                last_active_at = now()
            WHERE id = $1
            """,
            project_id,
            status,
            goals,
            description,
        )
        return {"id": project_id, "previous": dict(before) if before else None}

    async def add_event(
        self,
        project_id: UUID,
        *,
        summary: str,
        event_type: str = "update",
        detail: str | None = None,
        source_provider: str | None = None,
        source_conversation_id: UUID | None = None,
        provenance: str = "assistant_generated",
        conn: asyncpg.Connection | None = None,
    ) -> UUID | None:
        """Append a project event. Idempotent on (project, event text)."""
        executor = conn or self._db
        event_id = await executor.fetchval(
            """
            INSERT INTO project_events (
                project_id, event_type, summary, detail, source_provider,
                source_conversation_id, provenance, event_sha256
            ) VALUES ($1,$2,$3,$4,$5,$6,$7::pc_provenance,$8)
            ON CONFLICT (project_id, event_sha256) DO NOTHING
            RETURNING id
            """,
            project_id,
            event_type,
            summary,
            detail,
            source_provider,
            source_conversation_id,
            provenance,
            sha256_text(f"{event_type}:{summary}"),
        )
        if event_id:
            await executor.execute(
                "UPDATE projects SET last_active_at = now() WHERE id = $1", project_id
            )
        return event_id

    async def touch(self, project_id: UUID, conn: asyncpg.Connection | None = None) -> None:
        await (conn or self._db).execute(
            "UPDATE projects SET last_active_at = now() WHERE id = $1", project_id
        )

    async def snapshot(self, project_id: UUID, *, limit: int = 8) -> dict[str, Any]:
        """Compact current state of a project, for context packages."""
        project = await self._db.fetchrow(
            "SELECT id, slug, name, status, goals, description FROM projects WHERE id = $1",
            project_id,
        )
        if project is None:
            raise NotFoundError("Project not found")

        decisions = await self._db.fetch(
            """
            SELECT statement, decided_at FROM decisions
            WHERE project_id = $1 AND status = 'active'
            ORDER BY decided_at DESC LIMIT $2
            """,
            project_id,
            limit,
        )
        tasks = await self._db.fetch(
            """
            SELECT text, due_date, priority FROM tasks
            WHERE project_id = $1 AND status = 'open'
            ORDER BY priority DESC, due_date NULLS LAST LIMIT $2
            """,
            project_id,
            limit,
        )
        events = await self._db.fetch(
            """
            SELECT summary, created_at FROM project_events
            WHERE project_id = $1 ORDER BY created_at DESC LIMIT $2
            """,
            project_id,
            limit,
        )
        return {
            "project": dict(project),
            "decisions": [dict(r) for r in decisions],
            "open_tasks": [dict(r) for r in tasks],
            "recent_events": [dict(r) for r in events],
        }


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError):
        return False
    return True
