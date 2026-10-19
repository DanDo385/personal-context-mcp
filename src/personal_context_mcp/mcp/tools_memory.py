"""Durable memory and session checkpoint tools."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context_mcp.auth import scopes
from personal_context_mcp.auth.principal import current_principal
from personal_context_mcp.models import (
    CheckpointArtifactRef,
    CheckpointDecision,
    CheckpointFact,
    CheckpointListUpdate,
    CheckpointProjectUpdate,
    CheckpointResult,
    CheckpointTask,
    MemoryRecord,
    MemorySaveResult,
)
from personal_context_mcp.services import get_services


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="memory_save",
        tags={"memory", "write"},
        auth=require_scopes(scopes.MEMORY_WRITE),
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def memory_save(
        content: Annotated[
            str,
            Field(
                description=(
                    "The fact, stated so it makes sense on its own months from "
                    "now, without this conversation for context."
                )
            ),
        ],
        kind: Annotated[
            str,
            Field(description="fact, preference, relationship, decision or knowledge."),
        ] = "fact",
        subject: Annotated[
            str | None,
            Field(
                description=(
                    "Short handle for what this is about, used to deduplicate "
                    "against existing memories. e.g. 'obsidian-vault', 'coffee'."
                )
            ),
        ] = None,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        provenance: Annotated[
            str,
            Field(
                description=(
                    "user_statement if the user told you this; assistant_generated "
                    "if it is your own conclusion; external_source if you looked it "
                    "up. Be accurate - this is how future sessions tell the "
                    "difference."
                )
            ),
        ] = "user_statement",
        importance: Annotated[
            float,
            Field(
                ge=0.0,
                le=1.0,
                description="How much this matters in future sessions, 0 to 1. Leave at 0.5 unless the user said it matters more or less.",
            ),
        ] = 0.5,
        sensitivity: Annotated[
            str,
            Field(
                description=(
                    "normal, private, or highly_sensitive. highly_sensitive content "
                    "is never sent to external embedding or classification APIs."
                )
            ),
        ] = "normal",
    ) -> MemorySaveResult:
        """Save one durable fact to shared memory across all providers.

        Deduplicates: an identical or near-identical memory is updated rather
        than duplicated, and the previous wording is kept as a revision. Save
        facts, preferences, decisions and stable knowledge - not conversational
        noise and not things that will be stale next week.

        Prefer session_checkpoint when you have several things to record at
        once; it does this plus projects, tasks and lists in one transaction.
        """
        services = get_services()
        principal = current_principal()
        project_id = await services.projects.resolve(project)
        result = await services.memories.save(
            content=content,
            kind=kind,
            subject=subject,
            project_id=project_id,
            provenance=provenance,
            sensitivity=sensitivity,
            importance=importance,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        await services.audit.record(
            tool="memory_save",
            action="create" if result.outcome == "created" else "update",
            object_type="memory",
            object_id=result.id,
            principal=principal,
            provenance=provenance,
            resulting_version={"outcome": result.outcome},
        )
        return result

    @mcp.tool(
        name="memory_search",
        tags={"memory", "read"},
        auth=require_scopes(scopes.MEMORY_READ),
        annotations={"readOnlyHint": True},
    )
    async def memory_search(
        query: Annotated[str, Field(description="What to look for.")],
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results to return.")] = 10,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        kind: Annotated[str | None, Field(description="Filter by memory kind.")] = None,
    ) -> list[MemoryRecord]:
        """Search durable memories only.

        Narrower than context_search: use it when you specifically want stable
        facts and preferences rather than summaries, notes or decisions.
        """
        services = get_services()
        return await services.memories.search(
            query,
            limit=limit,
            project_id=await services.projects.resolve(project),
            kind=kind,
        )

    @mcp.tool(
        name="memory_history",
        tags={"memory", "read"},
        auth=require_scopes(scopes.MEMORY_READ),
        annotations={"readOnlyHint": True},
    )
    async def memory_history(
        memory_id: Annotated[
            UUID, Field(description="The memory's id, from memory_search or context results.")
        ],
    ) -> dict:
        """Every recorded version of one memory, newest first.

        Use to answer "when did I say that" or "what did this used to say".
        """
        services = get_services()
        record = await services.memories.get(memory_id)
        if record is None:
            return {"error": "No memory with that id."}
        return {
            "memory": record.model_dump(mode="json"),
            "revisions": await services.memories.history(memory_id),
            "audit": await services.audit.history(object_type="memory", object_id=memory_id),
        }

    @mcp.tool(
        name="session_checkpoint",
        tags={"sessions", "write"},
        auth=require_scopes(scopes.SESSIONS_WRITE, scopes.MEMORY_WRITE),
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def session_checkpoint(
        provider: Annotated[
            str, Field(description="Which app you are: chatgpt, claude, gemini or grok.")
        ],
        provider_conversation_id: Annotated[
            str | None,
            Field(
                description=(
                    "This conversation's id in your platform, if you can see it. "
                    "Supplying it makes repeat checkpoints update one record "
                    "instead of creating several."
                )
            ),
        ] = None,
        title: Annotated[str | None, Field(description="Short title for the session.")] = None,
        summary: Annotated[
            str | None,
            Field(
                description=(
                    "A few sentences on what was established. Write it for a "
                    "future session that cannot see this conversation."
                )
            ),
        ] = None,
        project: Annotated[
            str | None,
            Field(
                description=(
                    "The project this session was about (slug or name), when there is "
                    "one. Without it the session is often saved with no project and "
                    "never shows up in that project's handoff."
                )
            ),
        ] = None,
        decisions: Annotated[
            list[CheckpointDecision],
            Field(description="Decisions made in this conversation. Empty when none were made."),
        ] = [],
        facts: Annotated[
            list[CheckpointFact],
            Field(
                description="Durable facts worth remembering in future sessions. Not conversational noise."
            ),
        ] = [],
        tasks: Annotated[
            list[CheckpointTask],
            Field(description="Concrete unfinished actions. Each goes onto a list."),
        ] = [],
        list_updates: Annotated[
            list[CheckpointListUpdate],
            Field(description="Items to add to, complete on or remove from the user's real lists."),
        ] = [],
        project_updates: Annotated[
            list[CheckpointProjectUpdate],
            Field(description="Status or goal changes to existing projects."),
        ] = [],
        open_questions: Annotated[
            list[str], Field(description="Questions raised and left unresolved, one sentence each.")
        ] = [],
        artifacts: Annotated[
            list[CheckpointArtifactRef],
            Field(
                description="Artifacts this conversation produced or used, by artifact_id from an artifact tool."
            ),
        ] = [],
        important_context: Annotated[
            list[str],
            Field(
                description="Other context a future session needs that fits none of the fields above."
            ),
        ] = [],
        model: Annotated[str | None, Field(description="Your model name, for provenance.")] = None,
        sensitivity: Annotated[
            str, Field(description="normal, private or highly_sensitive.")
        ] = "normal",
    ) -> CheckpointResult:
        """Persist everything durable this session established, in one call.

        Call when the conversation has settled something worth carrying
        forward: a decision, a confirmed fact, a task, a list change, a project
        update, an unresolved question. Do NOT checkpoint small talk, a
        one-off lookup, or anything you already checkpointed - repeats are
        deduplicated but they cost a round trip.

        Everything is written in one transaction and is idempotent: calling
        twice with the same content does not produce duplicates. The result
        reports exactly what was created, updated and skipped, and how the
        content was classified and routed.
        """
        services = get_services()
        return await services.sessions.checkpoint(
            provider=provider,
            principal=current_principal(),
            provider_conversation_id=provider_conversation_id,
            title=title,
            summary=summary,
            project=project,
            decisions=decisions,
            facts=facts,
            tasks=tasks,
            list_updates=list_updates,
            project_updates=project_updates,
            open_questions=open_questions,
            artifacts=artifacts,
            important_context=important_context,
            model=model,
            sensitivity=sensitivity,
        )

    @mcp.tool(
        name="session_recent",
        tags={"sessions", "read"},
        auth=require_scopes(scopes.SESSIONS_READ),
        annotations={"readOnlyHint": True},
    )
    async def session_recent(
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results to return.")] = 10,
        provider: Annotated[str | None, Field(description="Filter by provider.")] = None,
        project: Annotated[str | None, Field(description="Filter by project.")] = None,
    ) -> list[dict]:
        """Recent session summaries with their open questions."""
        services = get_services()
        return await services.sessions.recent_summaries(
            limit=limit,
            provider=provider,
            project_id=await services.projects.resolve(project),
        )
