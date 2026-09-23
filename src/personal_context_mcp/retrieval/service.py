"""Context retrieval.

Hybrid: full-text plus vector (when embeddings exist), reranked by importance,
recency and project association. The interesting part is not the search, it is
the **budget**: `context_bootstrap` must return a package small enough to sit
in front of every request, so it walks the memory layers cheapest-first and
stops when the budget is spent.

    layer 3 durable memory      cheap, dense, almost always relevant
    layer 4 active project state
    layer 5 notes
    layer 2 session summaries
    layer 1 raw messages        only when nothing above answered

Raw transcripts are the last resort, never the default.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from personal_context_mcp.config import Settings
from personal_context_mcp.db.pool import Database
from personal_context_mcp.embeddings.service import EmbeddingService
from personal_context_mcp.models import ContextItem, ContextPackage, Provenance_
from personal_context_mcp.projects.service import ProjectService
from personal_context_mcp.util import estimate_tokens, truncate_for_budget

#: Per-item ceiling so one enormous note cannot eat the whole package.
_MAX_ITEM_TOKENS = 450


def _item(
    *,
    kind: str,
    row: asyncpg.Record | dict[str, Any],
    content: str,
    title: str | None = None,
    score: float | None = None,
    provenance_kind: str = "assistant_generated",
) -> ContextItem:
    trimmed = truncate_for_budget(content.strip(), _MAX_ITEM_TOKENS)
    return ContextItem(
        type=kind,  # type: ignore[arg-type]
        id=row["id"],
        title=title,
        content=trimmed,
        score=score,
        project=row.get("project_slug"),
        provenance=Provenance_(
            kind=row.get("provenance") or provenance_kind,  # type: ignore[arg-type]
            provider=row.get("source_provider") or row.get("provider"),
            conversation_id=row.get("source_conversation_id") or row.get("conversation_id"),
            recorded_at=row.get("updated_at") or row.get("created_at"),
            confidence=row.get("confidence"),
        ),
        estimated_tokens=estimate_tokens(trimmed),
    )


class RetrievalService:
    def __init__(
        self,
        db: Database,
        embeddings: EmbeddingService,
        projects: ProjectService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._embeddings = embeddings
        self._projects = projects
        self._settings = settings

    # ----------------------------------------------------------- searching

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project: str | None = None,
        provider: str | None = None,
        include_messages: bool = False,
    ) -> list[ContextItem]:
        """Hybrid search across every structured layer at once."""
        project_id = await self._projects.resolve(project) if project else None
        vector = await self._embeddings.embed_one(query) if self._embeddings.enabled else None

        items: list[ContextItem] = []
        items += await self._search_memories(query, vector, limit, project_id)
        items += await self._search_summaries(query, vector, limit, project_id, provider)
        items += await self._search_notes(query, vector, limit, project_id)
        items += await self._search_decisions(query, vector, limit, project_id)

        if include_messages:
            items += await self._search_messages(query, vector, limit, project_id, provider)

        items.sort(key=lambda i: i.score or 0.0, reverse=True)
        return items[:limit]

    async def _search_memories(
        self, query: str, vector: list[float] | None, limit: int, project_id: UUID | None
    ) -> list[ContextItem]:
        rows = await self._db.fetch(
            """
            WITH t AS (
                SELECT id, ts_rank_cd(search_tsv, websearch_to_tsquery('english', $1)) AS rank
                FROM memories WHERE search_tsv @@ websearch_to_tsquery('english', $1)
            ), v AS (
                SELECT id, 1 - (embedding <=> $2) AS rank FROM memories
                WHERE $2::vector IS NOT NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> $2 LIMIT 40
            ), m AS (
                SELECT id, max(rank) AS rank FROM (SELECT * FROM t UNION ALL SELECT * FROM v) u
                GROUP BY id
            )
            SELECT mem.*, p.slug AS project_slug,
                   m.rank * (0.55 + 0.45 * mem.importance)
                     * (CASE WHEN $3::uuid IS NOT NULL AND mem.project_id = $3
                             THEN 1.35 ELSE 1.0 END) AS score
            FROM m JOIN memories mem ON mem.id = m.id
            LEFT JOIN projects p ON p.id = mem.project_id
            WHERE mem.status = 'active'
            ORDER BY score DESC LIMIT $4
            """,
            query,
            vector,
            project_id,
            limit,
        )
        return [
            _item(kind="memory", row=r, content=r["content"], score=float(r["score"])) for r in rows
        ]

    async def _search_summaries(
        self,
        query: str,
        vector: list[float] | None,
        limit: int,
        project_id: UUID | None,
        provider: str | None,
    ) -> list[ContextItem]:
        rows = await self._db.fetch(
            """
            WITH t AS (
                SELECT id, ts_rank_cd(search_tsv, websearch_to_tsquery('english', $1)) AS rank
                FROM session_summaries
                WHERE search_tsv @@ websearch_to_tsquery('english', $1)
            ), v AS (
                SELECT id, 1 - (embedding <=> $2) AS rank FROM session_summaries
                WHERE $2::vector IS NOT NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> $2 LIMIT 30
            ), m AS (
                SELECT id, max(rank) AS rank FROM (SELECT * FROM t UNION ALL SELECT * FROM v) u
                GROUP BY id
            )
            SELECT s.*, p.slug AS project_slug, s.provider AS source_provider,
                   s.conversation_id,
                   m.rank * (CASE WHEN $3::uuid IS NOT NULL AND s.project_id = $3
                                  THEN 1.3 ELSE 1.0 END) AS score
            FROM m JOIN session_summaries s ON s.id = m.id
            LEFT JOIN projects p ON p.id = s.project_id
            WHERE ($4::text IS NULL OR s.provider = $4)
            ORDER BY score DESC LIMIT $5
            """,
            query,
            vector,
            project_id,
            provider,
            limit,
        )
        return [
            _item(
                kind="summary",
                row=r,
                content=r["summary"],
                title=r["title"],
                score=float(r["score"]),
            )
            for r in rows
        ]

    async def _search_notes(
        self, query: str, vector: list[float] | None, limit: int, project_id: UUID | None
    ) -> list[ContextItem]:
        rows = await self._db.fetch(
            """
            WITH t AS (
                SELECT id, ts_rank_cd(search_tsv, websearch_to_tsquery('english', $1)) AS rank
                FROM notes WHERE search_tsv @@ websearch_to_tsquery('english', $1)
            ), v AS (
                SELECT id, 1 - (embedding <=> $2) AS rank FROM notes
                WHERE $2::vector IS NOT NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> $2 LIMIT 30
            ), m AS (
                SELECT id, max(rank) AS rank FROM (SELECT * FROM t UNION ALL SELECT * FROM v) u
                GROUP BY id
            )
            SELECT n.*, p.slug AS project_slug, m.rank AS score
            FROM m JOIN notes n ON n.id = m.id
            LEFT JOIN projects p ON p.id = n.project_id
            WHERE ($3::uuid IS NULL OR n.project_id = $3)
            ORDER BY score DESC LIMIT $4
            """,
            query,
            vector,
            project_id,
            limit,
        )
        return [
            _item(
                kind="note", row=r, content=r["content"], title=r["title"], score=float(r["score"])
            )
            for r in rows
        ]

    async def _search_decisions(
        self, query: str, vector: list[float] | None, limit: int, project_id: UUID | None
    ) -> list[ContextItem]:
        rows = await self._db.fetch(
            """
            SELECT d.*, p.slug AS project_slug,
                   ts_rank_cd(d.search_tsv, websearch_to_tsquery('english', $1)) AS score
            FROM decisions d LEFT JOIN projects p ON p.id = d.project_id
            WHERE d.status = 'active'
              AND d.search_tsv @@ websearch_to_tsquery('english', $1)
              AND ($2::uuid IS NULL OR d.project_id = $2)
            ORDER BY score DESC, d.decided_at DESC LIMIT $3
            """,
            query,
            project_id,
            limit,
        )
        return [
            _item(
                kind="decision",
                row=r,
                content=r["statement"] + (f"\n\nWhy: {r['rationale']}" if r["rationale"] else ""),
                score=float(r["score"]),
            )
            for r in rows
        ]

    async def _search_messages(
        self,
        query: str,
        vector: list[float] | None,
        limit: int,
        project_id: UUID | None,
        provider: str | None,
    ) -> list[ContextItem]:
        rows = await self._db.fetch(
            """
            SELECT m.id, m.content, m.provenance, m.provider AS source_provider,
                   m.conversation_id, m.created_at, c.title, p.slug AS project_slug,
                   ts_rank_cd(m.search_tsv, websearch_to_tsquery('english', $1)) AS score
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            LEFT JOIN projects p ON p.id = c.project_id
            WHERE m.search_tsv @@ websearch_to_tsquery('english', $1)
              AND ($2::uuid IS NULL OR c.project_id = $2)
              AND ($3::text IS NULL OR m.provider = $3)
            ORDER BY score DESC, m.created_at DESC LIMIT $4
            """,
            query,
            project_id,
            provider,
            min(limit, 8),
        )
        return [
            _item(
                kind="message",
                row=r,
                content=r["content"],
                title=r["title"],
                score=float(r["score"]) * 0.7,
            )
            for r in rows
        ]

    # ------------------------------------------------------------- recent

    async def recent(
        self, *, limit: int = 10, provider: str | None = None, project: str | None = None
    ) -> list[ContextItem]:
        project_id = await self._projects.resolve(project) if project else None
        rows = await self._db.fetch(
            """
            SELECT s.id, s.title, s.summary, s.provider AS source_provider,
                   s.conversation_id, s.updated_at, p.slug AS project_slug
            FROM session_summaries s LEFT JOIN projects p ON p.id = s.project_id
            WHERE ($1::text IS NULL OR s.provider = $1)
              AND ($2::uuid IS NULL OR s.project_id = $2)
            ORDER BY s.updated_at DESC LIMIT $3
            """,
            provider,
            project_id,
            limit,
        )
        return [_item(kind="summary", row=r, content=r["summary"], title=r["title"]) for r in rows]

    # ---------------------------------------------------------- packages

    async def for_topic(
        self, topic: str, *, token_budget: int | None = None, project: str | None = None
    ) -> ContextPackage:
        return await self._build_package(
            query=topic,
            token_budget=token_budget or self._settings.default_token_budget,
            project=project,
            allow_raw_messages=True,
        )

    async def bootstrap(
        self, user_message: str, *, token_budget: int | None = None, project: str | None = None
    ) -> ContextPackage:
        """The package to put in front of a request.

        Always includes current project state and the most important durable
        memories, whether or not they matched the query - "what am I working
        on" is context the answer usually needs even when the words do not
        appear in the question.
        """
        return await self._build_package(
            query=user_message,
            token_budget=token_budget or self._settings.default_token_budget,
            project=project,
            allow_raw_messages=False,
            include_active_state=True,
        )

    async def _build_package(
        self,
        *,
        query: str,
        token_budget: int,
        project: str | None,
        allow_raw_messages: bool,
        include_active_state: bool = False,
    ) -> ContextPackage:
        spent = 0
        chosen: list[ContextItem] = []
        seen: set[UUID] = set()
        layers: list[str] = []
        notes: list[str] = []

        def take(items: list[ContextItem], layer: str, reserve: int = 0) -> bool:
            """Add items while budget allows. Returns False once full."""
            nonlocal spent
            added = False
            for item in items:
                if item.id in seen:
                    continue
                if spent + item.estimated_tokens > token_budget - reserve:
                    return False
                chosen.append(item)
                seen.add(item.id)
                spent += item.estimated_tokens
                added = True
            if added and layer not in layers:
                layers.append(layer)
            return True

        project_id = await self._projects.resolve(project) if project else None

        # Layer 4 first when bootstrapping: active project state is what a
        # continuation almost always needs.
        if include_active_state:
            take(await self._active_state(project_id), "project_state", reserve=600)

        # Layer 3.
        take(
            await self._search_memories(
                query,
                await self._embeddings.embed_one(query) if self._embeddings.enabled else None,
                12,
                project_id,
            ),
            "durable_memory",
            reserve=400,
        )

        # Layers 5 and 2.
        remaining = await self.search(query, limit=10, project=project)
        take([i for i in remaining if i.type in ("note", "decision")], "knowledge", reserve=250)
        take([i for i in remaining if i.type == "summary"], "session_summaries", reserve=100)

        # Layer 1 only if there is room left and nothing above answered well.
        truncated = False
        if allow_raw_messages and spent < token_budget * 0.6 and len(chosen) < 4:
            raw = await self._search_messages(query, None, 4, project_id, None)
            if raw:
                notes.append(
                    "Included raw message excerpts because the structured layers "
                    "returned little. These are verbatim transcript fragments."
                )
            take(raw, "raw_messages")

        if spent >= token_budget - 50:
            truncated = True

        if not chosen:
            notes.append(
                "Nothing in Personal Context matched. Answer from the current "
                "conversation; do not assume prior context exists."
            )

        return ContextPackage(
            query=truncate_for_budget(query, 60),
            items=chosen,
            token_budget=token_budget,
            estimated_tokens=spent,
            layers_used=layers,
            truncated=truncated,
            notes=notes,
        )

    async def _active_state(self, project_id: UUID | None) -> list[ContextItem]:
        """Current projects, their open tasks and live decisions."""
        items: list[ContextItem] = []

        projects = await self._db.fetch(
            """
            SELECT p.id, p.slug AS project_slug, p.name, p.status,
                   coalesce(p.goals, p.description, '') AS blurb, p.last_active_at
            FROM projects p
            WHERE p.status = 'active' AND ($1::uuid IS NULL OR p.id = $1)
            ORDER BY p.last_active_at DESC LIMIT 4
            """,
            project_id,
        )
        for row in projects:
            body = f"Project '{row['name']}' ({row['status']}). {row['blurb']}".strip()
            open_tasks = await self._db.fetch(
                """
                SELECT text FROM tasks WHERE project_id = $1 AND status = 'open'
                ORDER BY priority DESC, due_date NULLS LAST LIMIT 5
                """,
                row["id"],
            )
            if open_tasks:
                body += "\nOpen: " + "; ".join(t["text"] for t in open_tasks)
            items.append(
                _item(
                    kind="project",
                    row={**dict(row), "id": row["id"], "provenance": "system"},
                    content=body,
                    title=row["name"],
                    score=1.0,
                )
            )

        decisions = await self._db.fetch(
            """
            SELECT d.id, d.statement, d.rationale, d.provenance, d.source_provider,
                   d.source_conversation_id, d.decided_at AS updated_at,
                   p.slug AS project_slug
            FROM decisions d LEFT JOIN projects p ON p.id = d.project_id
            WHERE d.status = 'active' AND ($1::uuid IS NULL OR d.project_id = $1)
            ORDER BY d.decided_at DESC LIMIT 5
            """,
            project_id,
        )
        items += [
            _item(kind="decision", row=r, content=r["statement"], score=0.95) for r in decisions
        ]
        return items
