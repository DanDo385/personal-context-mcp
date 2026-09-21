"""Notes: the human-readable knowledge layer.

Notes live in Postgres and are *rendered* into the Obsidian vault by the sync
worker. Postgres holds the version history; the vault holds the copy a person
edits. When both change, the worker reports a conflict rather than picking a
winner.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from personal_context.db.pool import Database
from personal_context.embeddings.service import EmbeddingService
from personal_context.errors import NotFoundError
from personal_context.models import NoteRecord
from personal_context.util import sha256_text, slugify


def _record(row: asyncpg.Record, *, include_content: bool = False) -> NoteRecord:
    return NoteRecord(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        note_class=row["note_class"],
        project=row.get("project_slug"),
        vault_path=row["vault_path"],
        version=row["version"],
        updated_at=row["updated_at"],
        content=row["content"] if include_content else None,
    )


class NoteService:
    def __init__(self, db: Database, embeddings: EmbeddingService) -> None:
        self._db = db
        self._embeddings = embeddings

    async def resolve(self, reference: str) -> UUID | None:
        try:
            note_uuid: UUID | None = UUID(reference)
        except (ValueError, AttributeError):
            note_uuid = None
        row = await self._db.fetchrow(
            """
            SELECT id FROM notes
            WHERE ($1::uuid IS NOT NULL AND id = $1::uuid)
               OR slug = $2 OR lower(title) = lower($3)
            ORDER BY ($1::uuid IS NOT NULL AND id = $1::uuid) DESC
            LIMIT 1
            """,
            note_uuid,
            slugify(reference),
            reference,
        )
        return row["id"] if row else None

    async def upsert(
        self,
        *,
        title: str,
        content: str,
        note_class: str = "inbox",
        project_id: UUID | None = None,
        vault_path: str | None = None,
        sensitivity: str = "normal",
        citations: list[dict[str, Any]] | None = None,
        source_provider: str | None = None,
        source_conversation_id: UUID | None = None,
        provenance: str = "assistant_generated",
        author: str | None = None,
        append: bool = False,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool, int]:
        """Create or update a note. Returns (id, created, version).

        ``append=True`` adds to the existing body instead of replacing it,
        which is what research-to-obsidian wants when a topic is revisited.
        """
        executor = conn or self._db
        slug = slugify(title, fallback="note")
        existing = await executor.fetchrow(
            "SELECT id, content, version FROM notes WHERE slug = $1", slug
        )

        if existing and append and existing["content"] not in content:
            content = f"{existing['content'].rstrip()}\n\n---\n\n{content.strip()}"

        content = content.strip()
        content_hash = sha256_text(content)
        embedding = await self._embeddings.embed_one(
            f"{title}\n\n{content}", sensitivity=sensitivity
        )

        if existing is None:
            note_id = await executor.fetchval(
                """
                INSERT INTO notes (
                    slug, title, content, content_sha256, note_class, project_id,
                    vault_path, sensitivity, citations, source_provider,
                    source_conversation_id, provenance, embedding
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::pc_sensitivity,$9,$10,$11,
                          $12::pc_provenance,$13)
                RETURNING id
                """,
                slug,
                title.strip(),
                content,
                content_hash,
                note_class,
                project_id,
                vault_path,
                sensitivity,
                citations or [],
                source_provider,
                source_conversation_id,
                provenance,
                embedding,
            )
            await self._add_version(
                executor,
                note_id,
                1,
                title,
                content,
                content_hash,
                author or source_provider or "system",
            )
            return note_id, True, 1

        if existing["content"] == content:
            return existing["id"], False, existing["version"]

        version = existing["version"] + 1
        await executor.execute(
            """
            UPDATE notes
            SET title = $2, content = $3, content_sha256 = $4,
                note_class = coalesce($5, note_class),
                project_id = coalesce($6, project_id),
                vault_path = coalesce($7, vault_path),
                citations = CASE WHEN $8::jsonb = '[]'::jsonb THEN citations ELSE $8 END,
                version = $9,
                embedding = coalesce($10, embedding)
            WHERE id = $1
            """,
            existing["id"],
            title.strip(),
            content,
            content_hash,
            note_class,
            project_id,
            vault_path,
            citations or [],
            version,
            embedding,
        )
        await self._add_version(
            executor,
            existing["id"],
            version,
            title,
            content,
            content_hash,
            author or source_provider or "system",
        )
        return existing["id"], False, version

    @staticmethod
    async def _add_version(
        executor: Any,
        note_id: UUID,
        version: int,
        title: str,
        content: str,
        content_hash: str,
        author: str,
    ) -> None:
        await executor.execute(
            """
            INSERT INTO note_versions (note_id, version, title, content, content_sha256, author)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (note_id, version) DO NOTHING
            """,
            note_id,
            version,
            title,
            content,
            content_hash,
            author,
        )

    async def get(self, reference: str, *, include_content: bool = True) -> NoteRecord:
        note_id = await self.resolve(reference)
        if note_id is None:
            raise NotFoundError(f"No note matches {reference!r}.")
        row = await self._db.fetchrow(
            """
            SELECT n.*, p.slug AS project_slug
            FROM notes n LEFT JOIN projects p ON p.id = n.project_id WHERE n.id = $1
            """,
            note_id,
        )
        assert row is not None
        return _record(row, include_content=include_content)

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: UUID | None = None,
        note_class: str | None = None,
    ) -> list[NoteRecord]:
        vector = await self._embeddings.embed_one(query) if self._embeddings.enabled else None
        rows = await self._db.fetch(
            """
            WITH text_hits AS (
                SELECT id, ts_rank_cd(search_tsv, websearch_to_tsquery('english', $1)) AS rank
                FROM notes WHERE search_tsv @@ websearch_to_tsquery('english', $1)
            ),
            vector_hits AS (
                SELECT id, 1 - (embedding <=> $4) AS rank FROM notes
                WHERE $4::vector IS NOT NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> $4 LIMIT 30
            ),
            merged AS (
                SELECT id, max(rank) AS rank
                FROM (SELECT * FROM text_hits UNION ALL SELECT * FROM vector_hits) u
                GROUP BY id
            )
            SELECT n.*, p.slug AS project_slug, merged.rank
            FROM merged JOIN notes n ON n.id = merged.id
            LEFT JOIN projects p ON p.id = n.project_id
            WHERE ($2::uuid IS NULL OR n.project_id = $2)
              AND ($3::text IS NULL OR n.note_class = $3)
            ORDER BY merged.rank DESC, n.updated_at DESC
            LIMIT $5
            """,
            query,
            project_id,
            note_class,
            vector,
            limit,
        )
        return [_record(r) for r in rows]

    async def similar_titles(self, title: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Existing notes a new one might duplicate - fed to the classifier.

        Matches on shared significant words rather than trigram similarity, so
        this works without pg_trgm. The result only has to be good enough to
        show the classifier "here is what already exists with a similar name".
        """
        words = [w for w in title.lower().split() if len(w) > 3][:6]
        if not words:
            return []
        rows = await self._db.fetch(
            """
            SELECT slug, title, note_class,
                   (SELECT count(*) FROM unnest($1::text[]) w
                     WHERE lower(n.title) LIKE '%' || w || '%') AS overlap
            FROM notes n
            WHERE EXISTS (
                SELECT 1 FROM unnest($1::text[]) w
                WHERE lower(n.title) LIKE '%' || w || '%'
            )
            ORDER BY overlap DESC, n.updated_at DESC
            LIMIT $2
            """,
            words,
            limit,
        )
        return [dict(r) for r in rows]

    async def mark_synced(self, note_id: UUID, *, sha256: str) -> None:
        await self._db.execute(
            "UPDATE notes SET vault_synced_sha256 = $2, vault_synced_at = now() WHERE id = $1",
            note_id,
            sha256,
        )

    async def apply_vault_edit(
        self, *, vault_path: str, content: str, author: str = "human"
    ) -> dict[str, Any]:
        """Accept a human's edit from the vault as a new canonical version."""
        row = await self._db.fetchrow(
            "SELECT id, title, version, content_sha256 FROM notes WHERE vault_path = $1",
            vault_path,
        )
        if row is None:
            raise NotFoundError(f"No note is tracked at {vault_path!r}.")

        content_hash = sha256_text(content.strip())
        if content_hash == row["content_sha256"]:
            return {"note_id": str(row["id"]), "changed": False, "version": row["version"]}

        version = row["version"] + 1
        await self._db.execute(
            """
            UPDATE notes
            SET content = $2, content_sha256 = $3, version = $4,
                vault_synced_sha256 = $3, vault_synced_at = now(),
                provenance = 'user_statement'
            WHERE id = $1
            """,
            row["id"],
            content.strip(),
            content_hash,
            version,
        )
        await self._add_version(
            self._db, row["id"], version, row["title"], content.strip(), content_hash, author
        )
        return {"note_id": str(row["id"]), "changed": True, "version": version}
