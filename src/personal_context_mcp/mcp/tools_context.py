"""Context retrieval tools.

Tool descriptions are the main place a model learns *when* to call something,
so each one says what it is for and, just as importantly, when not to bother.
Results are compacted: nulls and empty containers are stripped before they
cross the MCP boundary.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context_mcp.auth import scopes
from personal_context_mcp.mcp.compact import dump
from personal_context_mcp.services import get_services


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
            int, Field(ge=200, le=4000, description="Approximate token ceiling for the result.")
        ] = 1200,
        project: Annotated[
            str | None, Field(description="Narrow to one project slug or name, if known.")
        ] = None,
    ) -> dict[str, Any]:
        """Smallest useful package of shared context. Skip for trivial questions.

        Call first when the request depends on earlier conversations, projects,
        decisions, preferences, lists or artifacts. Raw transcripts are included
        only when nothing else answered. Raise token_budget only if truncated.
        """
        services = get_services()
        package = await services.retrieval.bootstrap(
            user_message, token_budget=token_budget, project=project
        )
        return dump(package)

    @mcp.tool(
        name="context_search",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_search(
        query: Annotated[str, Field(description="What to look for, in natural language.")],
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        provider: Annotated[
            str | None,
            Field(description="chatgpt, claude, gemini or grok."),
        ] = None,
        include_messages: Annotated[
            bool,
            Field(description="Verbatim excerpts. Leave false; summaries are smaller."),
        ] = False,
    ) -> list[dict[str, Any]]:
        """Search memories, summaries, notes and decisions. Use after bootstrap."""
        services = get_services()
        items = await services.retrieval.search(
            query,
            limit=limit,
            project=project,
            provider=provider,
            include_messages=include_messages,
        )
        return [dump(item) for item in items]

    @mcp.tool(
        name="context_recent",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_recent(
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
        provider: Annotated[str | None, Field(description="Filter by provider.")] = None,
        project: Annotated[str | None, Field(description="Filter by project.")] = None,
    ) -> list[dict[str, Any]]:
        """Newest session summaries. Use for catch-up, not for a specific topic."""
        services = get_services()
        items = await services.retrieval.recent(limit=limit, provider=provider, project=project)
        return [dump(item) for item in items]

    @mcp.tool(
        name="context_for_topic",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_for_topic(
        topic: Annotated[str, Field(description="The topic to assemble context about.")],
        token_budget: Annotated[int, Field(ge=200, le=4000)] = 1200,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
    ) -> dict[str, Any]:
        """Budgeted package about one topic. Prefer this over include_messages."""
        services = get_services()
        package = await services.retrieval.for_topic(
            topic, token_budget=token_budget, project=project
        )
        return dump(package)
