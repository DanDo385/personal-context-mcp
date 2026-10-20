"""The note_write workflow, shared by normal notes and canonical list views."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from personal_context_mcp.audit.service import AuditService
from personal_context_mcp.auth.principal import Principal
from personal_context_mcp.classifier.policy import Verdict
from personal_context_mcp.classifier.service import ClassificationService
from personal_context_mcp.config import Settings
from personal_context_mcp.db.pool import Database
from personal_context_mcp.errors import ValidationError
from personal_context_mcp.homes import home_metadata
from personal_context_mcp.lists.service import ListService
from personal_context_mcp.notes.service import NoteService
from personal_context_mcp.obsidian.paths import VaultPaths
from personal_context_mcp.obsidian.queue import SyncQueue
from personal_context_mcp.projects.service import ProjectService
from personal_context_mcp.util import sha256_text


class NoteWriter:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        notes: NoteService,
        projects: ProjectService,
        classification: ClassificationService,
        lists: ListService,
        queue: SyncQueue,
        paths: VaultPaths,
        audit: AuditService,
    ) -> None:
        self.db, self.settings, self.notes, self.projects = db, settings, notes, projects
        self.classification, self.lists, self.queue, self.paths, self.audit = (
            classification,
            lists,
            queue,
            paths,
            audit,
        )

    async def write(
        self,
        *,
        principal: Principal,
        title: str,
        content: str,
        note_class: str | None = None,
        project: str | None = None,
        citations: list[dict] | None = None,
        append: bool = False,
        sensitivity: str = "normal",
        queue_for_vault: bool = True,
        home: str | None = None,
        concepts: list[str] | None = None,
        list_ref: str | None = None,
    ) -> dict:
        if sensitivity not in ("normal", "private", "highly_sensitive"):
            raise ValidationError("Unknown note sensitivity.")
        if list_ref:
            if content or append or citations:
                raise ValidationError(
                    "A list vault view uses its canonical items. Do not supply a prose copy."
                )
            return await self._list(
                list_ref,
                home=home,
                concepts=concepts,
                queue_for_vault=queue_for_vault,
                sensitivity=sensitivity,
            )
        if not title.strip() or not content.strip():
            raise ValidationError("A note needs a title and content.")
        existing_id = await self.notes.resolve(title)
        if existing_id:
            stored_level = await self.db.fetchval(
                "SELECT sensitivity FROM notes WHERE id = $1", existing_id
            )
            levels = {"normal": 0, "private": 1, "highly_sensitive": 2}
            sensitivity = max((sensitivity, stored_level), key=levels.__getitem__)
        project_id = await self.projects.resolve(project)
        if home and home.startswith("projects/"):
            project_id = await self.projects.resolve(home.removeprefix("projects/"))
            if project and project_id != await self.projects.resolve(project):
                raise ValidationError("The project and home disagree.")
        classification, event_id = None, None
        destination = note_class
        if destination is None:
            _, routing, event_id = await self.classification.classify_note(
                {
                    "title": title,
                    "content": content,
                    "existing_similar_notes": await self.notes.similar_titles(title),
                    "stated_project": project,
                    "sensitivity": sensitivity,
                }
            )
            destination = routing.destination or "inbox"
            if routing.sensitive is Verdict.YES and sensitivity == "normal":
                sensitivity = "private"
            classification = routing.as_json()
            if project_id is None and routing.project_slug:
                project_id = await self.projects.resolve(routing.project_slug)
        async with self.db.transaction() as conn:
            note_id, created, version = await self.notes.upsert(
                title=title,
                content=content,
                note_class=destination,
                project_id=project_id,
                sensitivity=sensitivity,
                citations=citations,
                source_provider=principal.provider if principal.provider != "unknown" else None,
                provenance="assistant_generated",
                append=append,
                home=home,
                concepts=concepts,
                conn=conn,
            )
            record = await conn.fetchrow("SELECT * FROM notes WHERE id = $1", note_id)
            assert record is not None
            metadata = record["metadata"]
            vault_path = self.paths.home_note(metadata["home"], title)
            status = "not_queued"
            if record["sensitivity"] != "normal":
                await conn.execute(
                    "UPDATE sync_jobs SET status = 'cancelled', completed_at = now() WHERE object_type = 'note' AND object_id = $1 AND status IN ('queued','approved')",
                    note_id,
                )
            if (
                queue_for_vault
                and self.settings.obsidian_enabled
                and record["sensitivity"] == "normal"
            ):
                await self._path(conn, "notes", note_id, record["vault_path"], vault_path)
                fingerprint = sha256_text(
                    json.dumps(
                        {
                            "version": version,
                            "content": record["content_sha256"],
                            "metadata": metadata,
                        },
                        sort_keys=True,
                    )
                )
                last = await conn.fetchrow(
                    "SELECT status, payload FROM sync_jobs WHERE object_type = 'note' AND object_id = $1 ORDER BY created_at DESC LIMIT 1",
                    note_id,
                )
                if last and last["payload"].get("fingerprint") == fingerprint:
                    status = (
                        "written"
                        if last["status"] == "done"
                        else "queued"
                        if last["status"] in ("approved", "queued", "leased")
                        else last["status"]
                    )
                else:
                    job = await self.queue.enqueue(
                        job_type="write_note",
                        object_type="note",
                        object_id=note_id,
                        vault_path=vault_path,
                        payload={
                            "note_id": str(note_id),
                            "version": version,
                            "fingerprint": fingerprint,
                        },
                        content_sha256=record["content_sha256"],
                        source_provider=principal.provider
                        if principal.provider != "unknown"
                        else None,
                        classification_event_id=event_id,
                        conn=conn,
                    )
                    status = "queued" if job else "busy"
        await self.audit.record(
            tool="note_write",
            action="create" if created else "update",
            object_type="note",
            object_id=note_id,
            principal=principal,
            provenance="assistant_generated",
            classification_event_id=event_id,
            resulting_version={"version": version, "note_class": destination},
        )
        return {
            "note_id": str(note_id),
            "created": created,
            "version": version,
            "note_class": destination,
            "home": metadata["home"],
            "tags": metadata["tags"],
            "vault_path": vault_path if status != "not_queued" else record["vault_path"],
            "queued_for_vault": status == "queued",
            "vault_status": status,
            "vault_note": self._message(status),
            "classification": classification,
        }

    async def _path(
        self, conn: Any, table: str, object_id: UUID, previous: str | None, desired: str
    ) -> None:
        # table is selected by this module, never supplied by an MCP caller.
        if previous and previous != desired:
            on_disk = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM sync_jobs WHERE vault_path = $1 AND status IN ('done','leased'))",
                previous,
            )
            if on_disk:
                raise ValidationError(
                    "This object already has a different vault path. Relocate the managed file before changing its home; no second copy was queued."
                )
            await conn.execute(
                "UPDATE sync_jobs SET status = 'cancelled', completed_at = now() WHERE vault_path = $1 AND status IN ('queued','approved')",
                previous,
            )
        query = {
            "notes": "UPDATE notes SET vault_path = $2 WHERE id = $1",
            "lists": "UPDATE lists SET vault_path = $2 WHERE id = $1",
        }[table]
        await conn.execute(query, object_id, desired)

    async def _list(
        self,
        reference: str,
        *,
        home: str | None,
        concepts: list[str] | None,
        queue_for_vault: bool,
        sensitivity: str,
    ) -> dict:
        list_id = await self.lists.require(reference)
        async with self.db.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT l.*, p.slug AS project_slug FROM lists l LEFT JOIN projects p ON p.id = l.project_id WHERE l.id = $1 FOR UPDATE OF l",
                list_id,
            )
            assert row is not None
            previous = row["metadata"]
            meta = home_metadata(
                home or previous.get("home"),
                row["project_slug"],
                concepts
                if concepts is not None
                else [
                    t.removeprefix("concept/")
                    for t in previous.get("tags", [])
                    if t.startswith("concept/")
                ],
            )
            path = self.paths.home_note(meta["home"], row["slug"], is_list=True)
            await conn.execute(
                "UPDATE lists SET metadata = metadata || $2::jsonb WHERE id = $1", list_id, meta
            )
            status = "not_queued"
            if queue_for_vault and self.settings.obsidian_enabled and sensitivity == "normal":
                await self._path(conn, "lists", list_id, row["vault_path"], path)
                await self.lists.queue_render(list_id, conn=conn)
                status = "queued"
        return {
            "list_id": str(list_id),
            "home": meta["home"],
            "tags": meta["tags"],
            "vault_path": path,
            "vault_status": status,
            "queued_for_vault": status == "queued",
            "vault_note": self._message(status),
        }

    @staticmethod
    def _message(status: str) -> str:
        if status == "written":
            return "Already written. The local obsidian-sync worker acknowledged this version."
        if status == "queued":
            return "Queued. The file appears once the local obsidian-sync worker runs."
        if status == "not_queued":
            return "Saved in the database; not queued for the vault (disabled, not requested, or sensitive)."
        return f"Vault write is {status}. It has not been confirmed written. Check sync_status."
