"""Project and note tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context_mcp.auth import scopes
from personal_context_mcp.auth.principal import current_principal
from personal_context_mcp.classifier.policy import Verdict
from personal_context_mcp.models import NoteRecord, ProjectRecord
from personal_context_mcp.services import get_services
from personal_context_mcp.util import sha256_text


def register(mcp: FastMCP) -> None:
    # ------------------------------------------------------------ projects

    @mcp.tool(
        name="project_list",
        tags={"projects", "read"},
        auth=require_scopes(scopes.PROJECTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def project_list(
        status: Annotated[
            str | None,
            Field(description="active, paused, done, archived, or null for all."),
        ] = "active",
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> list[ProjectRecord]:
        """The user's projects, most recently active first."""
        return await get_services().projects.list_all(status=status, limit=limit)

    @mcp.tool(
        name="project_search",
        tags={"projects", "read"},
        auth=require_scopes(scopes.PROJECTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def project_search(
        query: Annotated[str, Field(description="Words from the project name.")],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[ProjectRecord]:
        """Find a project by name. Call before project_create."""
        return await get_services().projects.search(query, limit=limit)

    @mcp.tool(
        name="project_get",
        tags={"projects", "read"},
        auth=require_scopes(scopes.PROJECTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def project_get(
        project: Annotated[str, Field(description="Project slug or name.")],
    ) -> dict:
        """Full current state of one project: goals, live decisions, open tasks
        and recent activity. This is the fastest way to resume work on it."""
        services = get_services()
        project_id = await services.projects.require(project)
        return await services.projects.snapshot(project_id)

    @mcp.tool(
        name="project_create",
        tags={"projects", "write"},
        auth=require_scopes(scopes.PROJECTS_WRITE),
        annotations={"idempotentHint": True},
    )
    async def project_create(
        name: Annotated[str, Field(description="Project name, as the user says it.")],
        description: str | None = None,
        goals: Annotated[str | None, Field(description="What finishing it looks like.")] = None,
    ) -> ProjectRecord:
        """Create a project. Call project_search first - a second project for
        work that already has one splits its history."""
        services = get_services()
        principal = current_principal()
        project_id, created = await services.projects.create(
            name=name,
            description=description,
            goals=goals,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        await services.audit.record(
            tool="project_create",
            action="create" if created else "read",
            object_type="project",
            object_id=project_id,
            principal=principal,
        )
        return await services.projects.get(str(project_id))

    @mcp.tool(
        name="project_update",
        tags={"projects", "write"},
        auth=require_scopes(scopes.PROJECTS_WRITE),
    )
    async def project_update(
        project: Annotated[str, Field(description="Project slug or name.")],
        status: Annotated[
            str | None, Field(description="active, paused, done or archived.")
        ] = None,
        goals: str | None = None,
        description: str | None = None,
    ) -> ProjectRecord:
        """Change a project's status, goals or description."""
        services = get_services()
        result = await services.projects.update(
            project, status=status, goals=goals, description=description
        )
        await services.audit.record(
            tool="project_update",
            action="update",
            object_type="project",
            object_id=result["id"],
            previous_version=result["previous"],
        )
        return await services.projects.get(project)

    @mcp.tool(
        name="project_event_add",
        tags={"projects", "write"},
        auth=require_scopes(scopes.PROJECTS_WRITE),
        annotations={"idempotentHint": True},
    )
    async def project_event_add(
        project: Annotated[str, Field(description="Project slug or name.")],
        summary: Annotated[str, Field(description="One line on what changed.")],
        event_type: Annotated[
            str, Field(description="status_change, milestone, note, blocker or update.")
        ] = "update",
        detail: str | None = None,
    ) -> dict:
        """Append an event to a project's log. Identical events are not repeated."""
        services = get_services()
        principal = current_principal()
        project_id = await services.projects.require(project)
        event_id = await services.projects.add_event(
            project_id,
            summary=summary,
            event_type=event_type,
            detail=detail,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        return {
            "added": event_id is not None,
            "event_id": str(event_id) if event_id else None,
            "reason": None if event_id else "An identical event is already recorded.",
        }

    # --------------------------------------------------------------- notes

    @mcp.tool(
        name="note_search",
        tags={"notes", "read"},
        auth=require_scopes(scopes.NOTES_READ),
        annotations={"readOnlyHint": True},
    )
    async def note_search(
        query: Annotated[str, Field(description="What to look for.")],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        project: str | None = None,
        note_class: Annotated[
            str | None,
            Field(description="project, research, wiki, concept, resource, daily or inbox."),
        ] = None,
    ) -> list[NoteRecord]:
        """Search the user's written notes."""
        services = get_services()
        return await services.notes.search(
            query,
            limit=limit,
            project_id=await services.projects.resolve(project),
            note_class=note_class,
        )

    @mcp.tool(
        name="note_get",
        tags={"notes", "read"},
        auth=require_scopes(scopes.NOTES_READ),
        annotations={"readOnlyHint": True},
    )
    async def note_get(
        note: Annotated[str, Field(description="Note slug, title or id.")],
    ) -> NoteRecord:
        """Read one note in full."""
        return await get_services().notes.get(note)

    @mcp.tool(
        name="note_write",
        tags={"notes", "write"},
        auth=require_scopes(scopes.NOTES_WRITE),
        annotations={"idempotentHint": True},
    )
    async def note_write(
        title: Annotated[str, Field(description="Note title. Reusing a title updates that note.")],
        content: Annotated[str, Field(description="The note body, in Markdown.")],
        note_class: Annotated[
            str | None,
            Field(
                description=(
                    "Where it belongs: project, research, wiki, concept, resource, "
                    "daily or inbox. Leave unset to let the classifier decide."
                )
            ),
        ] = None,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        citations: Annotated[
            list[dict],
            Field(description="Sources, as objects with 'title' and 'url'."),
        ] = [],
        append: Annotated[
            bool,
            Field(description="Add to the existing note rather than replacing it."),
        ] = False,
        sensitivity: Annotated[
            str, Field(description="normal, private or highly_sensitive.")
        ] = "normal",
        queue_for_vault: Annotated[
            bool,
            Field(
                description=(
                    "Queue a write into the Obsidian vault. The write happens when "
                    "the local sync worker next runs; it does not happen here."
                )
            ),
        ] = True,
    ) -> dict:
        """Write durable research or knowledge as a note.

        The note is stored canonically in the database. If queue_for_vault is
        true it is ALSO queued for the user's Obsidian vault - queued, not
        written: this service has no access to their filesystem, and the write
        only lands when their local worker next polls.
        """
        services = get_services()
        principal = current_principal()
        project_id = await services.projects.resolve(project)

        classification = None
        destination = note_class
        if destination is None:
            state = {
                "title": title,
                "content": content,
                "existing_similar_notes": await services.notes.similar_titles(title),
                "stated_project": project,
                "sensitivity": sensitivity,
            }
            _, routing, event_id = await services.classification.classify_note(state)
            destination = routing.destination or "inbox"
            if routing.sensitive is Verdict.YES:
                sensitivity = "private"
            classification = routing.as_json()
            if project_id is None and routing.project_slug:
                project_id = await services.projects.resolve(routing.project_slug)
        else:
            event_id = None

        note_id, created, version = await services.notes.upsert(
            title=title,
            content=content,
            note_class=destination,
            project_id=project_id,
            sensitivity=sensitivity,
            citations=citations,
            source_provider=principal.provider if principal.provider != "unknown" else None,
            provenance="assistant_generated",
            append=append,
        )

        queued = False
        vault_path = None
        if queue_for_vault and services.settings.obsidian_enabled and sensitivity == "normal":
            project_slug = None
            if project_id:
                project_slug = await services.db.fetchval(
                    "SELECT slug FROM projects WHERE id = $1", project_id
                )
            vault_path = services.vault_paths.note(
                note_class=destination, title=title, project_slug=project_slug
            )
            await services.db.execute(
                "UPDATE notes SET vault_path = coalesce(vault_path, $2) WHERE id = $1",
                note_id,
                vault_path,
            )
            record = await services.notes.get(str(note_id))
            queued = bool(
                await services.sync_queue.enqueue(
                    job_type="write_note",
                    object_type="note",
                    object_id=note_id,
                    vault_path=vault_path,
                    payload={"note_id": str(note_id), "version": version},
                    content_sha256=sha256_text(record.content or content),
                    source_provider=(
                        principal.provider if principal.provider != "unknown" else None
                    ),
                    classification_event_id=event_id,
                )
            )

        await services.audit.record(
            tool="note_write",
            action="create" if created else "update",
            object_type="note",
            object_id=note_id,
            principal=principal,
            classification_event_id=event_id,
            provenance="assistant_generated",
            resulting_version={"version": version, "note_class": destination},
        )

        return {
            "note_id": str(note_id),
            "created": created,
            "version": version,
            "note_class": destination,
            "vault_path": vault_path,
            "queued_for_vault": queued,
            "vault_note": (
                "Queued. The file appears once the local obsidian-sync worker runs."
                if queued
                else "Not queued for the vault."
            ),
            "classification": classification,
        }
