"""Lists and list items.

Postgres is canonical. Obsidian renders a view of a list, and a checkbox
ticked in that view flows back here through the sync worker - which is why
every rendered item carries its UUID in an HTML comment. Identity is never
inferred from item text alone.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from personal_context_mcp.db.pool import Database
from personal_context_mcp.errors import NotFoundError, ValidationError
from personal_context_mcp.models import ListItemRecord, ListRecord
from personal_context_mcp.util import normalized_sha256, slugify


def _list_record(row: asyncpg.Record) -> ListRecord:
    return ListRecord(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        description=row["description"],
        list_type=row["list_type"],
        project=row.get("project_slug"),
        status=row["status"],
        open_items=row.get("open_items", 0) or 0,
        total_items=row.get("total_items", 0) or 0,
    )


def _item_record(row: asyncpg.Record) -> ListItemRecord:
    return ListItemRecord(
        id=row["id"],
        list_id=row["list_id"],
        list_slug=row.get("list_slug", ""),
        text=row["text"],
        status=row["status"],
        priority=row["priority"],
        position=row["position"],
        due_date=row["due_date"],
        notes=row["notes"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


class ListService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---------------------------------------------------------------- lists

    async def resolve(self, reference: str) -> UUID | None:
        """Resolve a UUID, slug or display name to a list id.

        A UUID has to work: tools that have just created a list hand its id
        straight back to `get()`.
        """
        reference = (reference or "").strip()
        if not reference:
            return None
        try:
            as_uuid: UUID | None = UUID(reference)
        except (ValueError, AttributeError):
            as_uuid = None

        row = await self._db.fetchrow(
            """
            SELECT id FROM lists
            WHERE ($1::uuid IS NOT NULL AND id = $1::uuid)
               OR slug = $2 OR lower(name) = lower($3)
            ORDER BY ($1::uuid IS NOT NULL AND id = $1::uuid) DESC, (slug = $2) DESC
            LIMIT 1
            """,
            as_uuid,
            slugify(reference),
            reference,
        )
        return row["id"] if row else None

    async def require(self, reference: str) -> UUID:
        list_id = await self.resolve(reference)
        if list_id is None:
            raise NotFoundError(
                f"No list matches {reference!r}. Call list_search first - the list "
                "probably exists under a slightly different name. Only call "
                "list_create if it genuinely does not exist."
            )
        return list_id

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        list_type: str = "general",
        project_id: UUID | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool]:
        executor = conn or self._db
        row = await executor.fetchrow(
            """
            INSERT INTO lists (slug, name, description, list_type, project_id)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (slug) DO UPDATE
                SET description = coalesce(lists.description, EXCLUDED.description),
                    updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            slugify(name),
            name.strip(),
            description,
            list_type,
            project_id,
        )
        if row is None:  # pragma: no cover - the upsert always returns a row
            raise RuntimeError("list upsert returned no row")
        return row["id"], bool(row["created"])

    async def get_or_create(
        self, reference: str, *, list_type: str = "general", conn: asyncpg.Connection | None = None
    ) -> UUID:
        existing = await self.resolve(reference)
        if existing:
            return existing
        list_id, _ = await self.create(name=reference, list_type=list_type, conn=conn)
        return list_id

    async def search(self, query: str = "", *, limit: int = 20) -> list[ListRecord]:
        rows = await self._db.fetch(
            """
            SELECT l.*, p.slug AS project_slug,
                   (SELECT count(*) FROM list_items i
                     WHERE i.list_id = l.id AND i.status = 'open') AS open_items,
                   (SELECT count(*) FROM list_items i WHERE i.list_id = l.id) AS total_items,
                   CASE
                     WHEN $1 = '' THEN 0
                     WHEN lower(l.name) = lower($1) THEN 3
                     WHEN l.name ILIKE $1 || '%' THEN 2
                     ELSE 1
                   END AS name_rank
            FROM lists l
            LEFT JOIN projects p ON p.id = l.project_id
            WHERE l.status = 'active'
              AND ($1 = '' OR l.search_tsv @@ websearch_to_tsquery('english', $1)
                           OR l.name ILIKE '%' || $1 || '%')
            ORDER BY name_rank DESC, l.updated_at DESC
            LIMIT $2
            """,
            query,
            limit,
        )
        return [_list_record(r) for r in rows]

    async def get(self, reference: str) -> ListRecord:
        list_id = await self.require(reference)
        row = await self._db.fetchrow(
            """
            SELECT l.*, p.slug AS project_slug,
                   (SELECT count(*) FROM list_items i
                     WHERE i.list_id = l.id AND i.status = 'open') AS open_items,
                   (SELECT count(*) FROM list_items i WHERE i.list_id = l.id) AS total_items
            FROM lists l LEFT JOIN projects p ON p.id = l.project_id
            WHERE l.id = $1
            """,
            list_id,
        )
        if row is None:  # pragma: no cover - require() already proved it exists
            raise NotFoundError("List disappeared between lookup and read.")
        return _list_record(row)

    async def archive(self, reference: str) -> UUID:
        list_id = await self.require(reference)
        await self._db.execute("UPDATE lists SET status = 'archived' WHERE id = $1", list_id)
        return list_id

    # ---------------------------------------------------------------- items

    async def items(
        self,
        reference: str,
        *,
        status: str | None = "open",
        limit: int = 200,
    ) -> list[ListItemRecord]:
        list_id = await self.require(reference)
        rows = await self._db.fetch(
            """
            SELECT i.*, l.slug AS list_slug
            FROM list_items i JOIN lists l ON l.id = i.list_id
            WHERE i.list_id = $1 AND ($2::text IS NULL OR i.status = $2)
            ORDER BY i.status = 'done', i.priority DESC, i.position, i.created_at
            LIMIT $3
            """,
            list_id,
            status,
            limit,
        )
        return [_item_record(r) for r in rows]

    async def add_item(
        self,
        *,
        list_id: UUID,
        text: str,
        notes: str | None = None,
        priority: int = 0,
        due_date: date | None = None,
        source_provider: str | None = None,
        source_conversation_id: UUID | None = None,
        provenance: str = "user_statement",
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool]:
        """Add an item. Returns (id, created); an existing match is not duplicated."""
        text = text.strip()
        if not text:
            raise ValidationError("List item text is empty.")

        executor = conn or self._db
        position = await executor.fetchval(
            "SELECT coalesce(max(position), 0) + 1 FROM list_items WHERE list_id = $1",
            list_id,
        )
        row = await executor.fetchrow(
            """
            INSERT INTO list_items (
                list_id, text, text_sha256, notes, priority, position, due_date,
                source_provider, source_conversation_id, provenance
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::pc_provenance)
            ON CONFLICT (list_id, text_sha256) DO UPDATE
                SET notes = coalesce(EXCLUDED.notes, list_items.notes),
                    priority = greatest(list_items.priority, EXCLUDED.priority),
                    due_date = coalesce(EXCLUDED.due_date, list_items.due_date),
                    updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            list_id,
            text,
            normalized_sha256(text),
            notes,
            priority,
            position,
            due_date,
            source_provider,
            source_conversation_id,
            provenance,
        )
        if row is None:  # pragma: no cover - the upsert always returns a row
            raise RuntimeError("list item upsert returned no row")
        await executor.execute("UPDATE lists SET updated_at = now() WHERE id = $1", list_id)
        return row["id"], bool(row["created"])

    async def resolve_item(self, list_id: UUID, reference: str) -> UUID | None:
        """Find an item by UUID or by its text. IDs win; text is a fallback."""
        try:
            item_uuid = UUID(reference)
        except (ValueError, AttributeError):
            item_uuid = None

        row = await self._db.fetchrow(
            """
            SELECT id FROM list_items
            WHERE list_id = $1
              AND (($2::uuid IS NOT NULL AND id = $2::uuid) OR text_sha256 = $3)
            ORDER BY ($2::uuid IS NOT NULL AND id = $2::uuid) DESC
            LIMIT 1
            """,
            list_id,
            item_uuid,
            normalized_sha256(reference),
        )
        return row["id"] if row else None

    async def update_item(
        self,
        item_id: UUID,
        *,
        text: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        due_date: date | None = None,
        notes: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        executor = conn or self._db
        before = await executor.fetchrow(
            "SELECT text, status, priority, due_date, notes FROM list_items WHERE id = $1",
            item_id,
        )
        if before is None:
            raise NotFoundError("List item not found.")

        await executor.execute(
            """
            UPDATE list_items
            SET text = coalesce($2, text),
                text_sha256 = CASE WHEN $2 IS NULL THEN text_sha256 ELSE $3 END,
                status = coalesce($4, status),
                priority = coalesce($5, priority),
                due_date = coalesce($6, due_date),
                notes = coalesce($7, notes),
                completed_at = CASE
                    WHEN $4 = 'done' THEN coalesce(completed_at, now())
                    WHEN $4 IS NOT NULL AND $4 <> 'done' THEN NULL
                    ELSE completed_at END
            WHERE id = $1
            """,
            item_id,
            text,
            normalized_sha256(text) if text else None,
            status,
            priority,
            due_date,
            notes,
        )
        return dict(before)

    async def complete_item(
        self, item_id: UUID, conn: asyncpg.Connection | None = None
    ) -> dict[str, Any]:
        return await self.update_item(item_id, status="done", conn=conn)

    async def move_item(
        self, item_id: UUID, *, target_list_id: UUID, conn: asyncpg.Connection | None = None
    ) -> None:
        executor = conn or self._db
        position = await executor.fetchval(
            "SELECT coalesce(max(position), 0) + 1 FROM list_items WHERE list_id = $1",
            target_list_id,
        )
        # A move onto a list that already has the same text collapses into the
        # existing item rather than creating a second copy.
        moved = await executor.fetchval(
            """
            UPDATE list_items SET list_id = $2, position = $3 WHERE id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM list_items other
                  WHERE other.list_id = $2 AND other.text_sha256 = list_items.text_sha256
              )
            RETURNING id
            """,
            item_id,
            target_list_id,
            position,
        )
        if moved is None:
            await executor.execute("DELETE FROM list_items WHERE id = $1", item_id)

    async def recent_items(self, *, limit: int = 20) -> list[ListItemRecord]:
        rows = await self._db.fetch(
            """
            SELECT i.*, l.slug AS list_slug
            FROM list_items i JOIN lists l ON l.id = i.list_id
            WHERE l.status = 'active'
            ORDER BY i.updated_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [_item_record(r) for r in rows]

    async def search_items(
        self, query: str, *, limit: int = 20, status: str | None = None
    ) -> list[ListItemRecord]:
        rows = await self._db.fetch(
            """
            SELECT i.*, l.slug AS list_slug,
                   ts_rank_cd(i.search_tsv, websearch_to_tsquery('english', $1)) AS rank
            FROM list_items i JOIN lists l ON l.id = i.list_id
            WHERE l.status = 'active'
              AND (i.search_tsv @@ websearch_to_tsquery('english', $1)
                   OR i.text ILIKE '%' || $1 || '%')
              AND ($2::text IS NULL OR i.status = $2)
            ORDER BY rank DESC, i.updated_at DESC
            LIMIT $3
            """,
            query,
            status,
            limit,
        )
        return [_item_record(r) for r in rows]
