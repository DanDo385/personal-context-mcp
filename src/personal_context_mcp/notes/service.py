"""Notes: the human-readable knowledge layer.

Notes live in Postgres and are *rendered* into the Obsidian vault by the sync
worker. Postgres holds the version history; the vault holds the copy a person
edits. When both change, the worker reports a conflict rather than picking a
winner.
"""

from __future__ import annotations

from typing import Any, get_args
from uuid import UUID

import asyncpg

from personal_context_mcp.db.pool import Database
from personal_context_mcp.embeddings.service import EmbeddingService
from personal_context_mcp.errors import NotFoundError
from personal_context_mcp.homes import home_metadata
from personal_context_mcp.models import NoteClass, NoteRecord
from personal_context_mcp.obsidian.render import extract_note_body
from personal_context_mcp.util import sha256_text, slugify

_NOTE_CLASSES = frozenset(get_args(NoteClass))


def _record(row: asyncpg.Record, *, include_content: bool = False) -> NoteRecord:
    return NoteRecord(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        # A stored value outside the vocabulary must not fail a whole search
        # result; it reads as inbox, where an unsorted note belongs anyway.
        note_class=row["note_class"] if row["note_class"] in _NOTE_CLASSES else "inbox",
        project=row.get("project_slug"),
        vault_path=row["vault_path"],
        version=row["version"],
        updated_at=row["updated_at"],
        content=row["content"] if include_content else None,
        home=(row.get("metadata") or {}).get("home"),
        tags=(row.get("metadata") or {}).get("tags", []),
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
        home: str | None = None,
        concepts: list[str] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool, int]:
        """Create or update a note. Returns (id, created, version).

        ``append=True`` adds to the existing body instead of replacing it,
        which is what research-to-obsidian wants when a topic is revisited.
        """
        if conn is None:
            async with self._db.transaction() as tx:
                return await self.upsert(
                    title=title,
                    content=content,
                    note_class=note_class,
                    project_id=project_id,
                    vault_path=vault_path,
                    sensitivity=sensitivity,
                    citations=citations,
                    source_provider=source_provider,
                    source_conversation_id=source_conversation_id,
                    provenance=provenance,
                    author=author,
                    append=append,
                    home=home,
                    concepts=concepts,
                    conn=tx,
                )
        executor = conn
        slug = slugify(title, fallback="note")
        # One writer per slug until commit: concurrent extractions that land on
        # the same title would otherwise both see no row and both insert.
        await executor.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"notes:{slug}")
        existing = await executor.fetchrow(
            "SELECT id, content, version, metadata, project_id, sensitivity FROM notes WHERE slug = $1",
            slug,
        )

        project_slug = await executor.fetchval(
            "SELECT slug FROM projects WHERE id = $1",
            project_id or (existing["project_id"] if existing else None),
        )
        previous = (existing["metadata"] or {}) if existing else {}
        if existing:
            levels = {"normal": 0, "private": 1, "highly_sensitive": 2}
            sensitivity = max((sensitivity, existing["sensitivity"]), key=levels.__getitem__)
        metadata = home_metadata(
            home or previous.get("home"),
            project_slug,
            concepts
            if concepts is not None
            else [
                t.removeprefix("concept/")
                for t in previous.get("tags", [])
                if t.startswith("concept/")
            ],
        )

        metadata["tags"] = [
            t
            for t in previous.get("tags", [])
            if t != previous.get("home") and not t.startswith("concept/")
        ] + metadata["tags"]

        if append:
            incoming_hash = sha256_text(content.strip())
            appended = previous.get("append_hashes", [])
            if existing and incoming_hash in appended:
                content = existing["content"]
            elif existing and existing["content"] not in content:
                content = f"{existing['content'].rstrip()}\n\n---\n\n{content.strip()}"
            metadata["append_hashes"] = sorted({*appended, incoming_hash})

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
                    source_conversation_id, provenance, embedding, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::pc_sensitivity,$9,$10,$11,
                          $12::pc_provenance,$13,$14)
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
                metadata,
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

        await executor.execute(
            "UPDATE notes SET metadata = metadata || $2::jsonb, sensitivity = $3::pc_sensitivity WHERE id = $1",
            existing["id"],
            metadata,
            sensitivity,
        )
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
        self,
        *,
        vault_path: str,
        content: str,
        author: str = "human",
        file_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Accept a human's edit from the vault as a new canonical version.

        ``content`` is the whole file. Only the human-owned body is stored:
        the frontmatter, the title heading and the generated block (links,
        maps, sources) are the renderer's and are stripped, or every edit
        would nest another copy of them into the note.
        """
        row = await self._db.fetchrow(
            "SELECT id, title, version, content_sha256 FROM notes WHERE vault_path = $1",
            vault_path,
        )
        if row is None:
            raise NotFoundError(f"No note is tracked at {vault_path!r}.")

        body = extract_note_body(content, row["title"])
        content_hash = sha256_text(body)
        if content_hash == row["content_sha256"]:
            if file_sha256:
                await self.mark_synced(row["id"], sha256=file_sha256)
            return {"note_id": str(row["id"]), "changed": False, "version": row["version"]}

        version = row["version"] + 1
        await self._db.execute(
            """
            UPDATE notes
            SET content = $2, content_sha256 = $3, version = $4,
                vault_synced_sha256 = coalesce($5, vault_synced_sha256), vault_synced_at = now(),
                provenance = 'user_statement'
            WHERE id = $1
            """,
            row["id"],
            body,
            content_hash,
            version,
            file_sha256,
        )
        await self._add_version(
            self._db, row["id"], version, row["title"], body, content_hash, author
        )
        return {"note_id": str(row["id"]), "changed": True, "version": version}
