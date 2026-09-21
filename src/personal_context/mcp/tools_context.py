"""Context retrieval tools.

Tool descriptions are the main place a model learns *when* to call something,
so each one says what it is for and, just as importantly, when not to bother.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context.auth import scopes
from personal_context.models import ContextItem, ContextPackage
from personal_context.services import get_services


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="context_bootstrap",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_bootstrap(
        user_message: Annotated[
            str,
            Field(description="The user's message, verbatim. Do not summarise it first."),
        ],
        token_budget: Annotated[
            int, Field(ge=200, le=12000, description="Approximate token ceiling for the result.")
        ] = 2500,
        project: Annotated[
            str | None, Field(description="Narrow to one project slug or name, if known.")
        ] = None,
    ) -> ContextPackage:
        """Retrieve the smallest useful package of the user's shared context.

        Call this FIRST when the request could depend on earlier conversations,
        projects, decisions, preferences, research, lists, people or artifacts -
        including any time the user says "we discussed", "remember", "continue",
        or "what did we decide". Skip it for trivial self-contained questions.

        Returns durable memories, active project state, decisions, open tasks,
        relevant notes and recent session summaries, each with provenance saying
        whether it came from the user directly or from a model's analysis.
        Raw transcript excerpts are included only when nothing else answered.
        """
        services = get_services()
        return await services.retrieval.bootstrap(
            user_message, token_budget=token_budget, project=project
        )

    @mcp.tool(
        name="context_search",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_search(
        query: Annotated[str, Field(description="What to look for, in natural language.")],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        provider: Annotated[
            str | None,
            Field(description="Restrict to one provider: chatgpt, claude, gemini or grok."),
        ] = None,
        include_messages: Annotated[
            bool,
            Field(
                description=(
                    "Include verbatim message excerpts. Leave false unless you need "
                    "the exact words - summaries and memories are usually enough and "
                    "far smaller."
                )
            ),
        ] = False,
    ) -> list[ContextItem]:
        """Search memories, summaries, notes and decisions across every provider.

        Use after context_bootstrap when you need more precision on a specific
        topic, or when the user refers to something you could not find.
        """
        services = get_services()
        return await services.retrieval.search(
            query,
            limit=limit,
            project=project,
            provider=provider,
            include_messages=include_messages,
        )

    @mcp.tool(
        name="context_recent",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_recent(
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        provider: Annotated[str | None, Field(description="Filter by provider.")] = None,
        project: Annotated[str | None, Field(description="Filter by project.")] = None,
    ) -> list[ContextItem]:
        """Most recent session summaries, newest first.

        Use for "what was I working on recently" or "catch me up", where
        recency matters more than relevance to a particular query.
        """
        services = get_services()
        return await services.retrieval.recent(limit=limit, provider=provider, project=project)

    @mcp.tool(
        name="context_for_topic",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_for_topic(
        topic: Annotated[str, Field(description="The topic to assemble context about.")],
        token_budget: Annotated[int, Field(ge=200, le=12000)] = 2500,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
    ) -> ContextPackage:
        """Assemble a budgeted package about one topic.

        Like context_bootstrap but aimed at a topic rather than a specific
        message, and willing to fall back to raw transcript excerpts when the
        structured layers are thin.
        """
        services = get_services()
        return await services.retrieval.for_topic(topic, token_budget=token_budget, project=project)
