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
from personal_context_mcp.mcp.result import ToolResult, object_output_schema, text_and_structured
from personal_context_mcp.services import get_services


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="context_bootstrap",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
        output_schema=object_output_schema(),
    )
    async def context_bootstrap(
        query: Annotated[
            str | None,
            Field(
                description=(
                    "A few key terms you choose, like your own memory search: names, "
                    "projects, topics (e.g. 'harbor notes deploy'). Not the user's message. "
                    "Leave empty to get only the project's handoff."
                )
            ),
        ] = None,
        project: Annotated[
            str | None,
            Field(description="Project slug or name, when you know it. Inferred otherwise."),
        ] = None,
        token_budget: Annotated[
            int, Field(ge=200, le=4000, description="Approximate token ceiling for the result.")
        ] = 1200,
        user_message: Annotated[
            str | None,
            Field(
                description=(
                    "Deprecated: use query. If sent, only its key terms are searched, "
                    "never the whole text."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Start a chat that continues earlier work: one call, one package.

        Call only when the request depends on past work: a project, earlier
        decisions, lists, "remember", "last time", a continuing task.
        Skip for trivial questions and anything self-contained.

        Returns the project's handoff first (latest session summaries with
        their open questions, open tasks, active decisions), then items
        matching query, then raw transcript excerpts only if nothing else
        matched. Says which project it used and why, which layers ran, and
        flags decisions a newer item may overrule. Raise token_budget only if
        truncated. The text result is one line of counts; the package is the
        structured result.
        """
        services = get_services()
        package = await services.retrieval.bootstrap(
            query, user_message=user_message, token_budget=token_budget, project=project
        )
        payload = dump(package)
        items = payload.get("items") or []
        count = len(items)
        noun = "item" if count == 1 else "items"
        line = f"{count} {noun}, {payload['estimated_tokens']} of {payload['token_budget']} tokens"
        if payload.get("truncated"):
            line = f"{line}, truncated"
        return text_and_structured(line, payload)

    @mcp.tool(
        name="context_search",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_search(
        query: Annotated[
            str,
            Field(
                description=(
                    "Key terms you choose, not the user's message: names, topics, ids. "
                    'Use "quoted phrases" for exact wording and -word to exclude.'
                )
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum results to return.")] = 5,
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
        """Ranked search over decisions, memories, summaries, notes, lists and artifacts.

        Use when bootstrap did not cover what you need. Results match any of the
        terms, rank higher the more they match, and favour the user's own words
        over an assistant's summary and recent items over old ones. An empty
        result means nothing matched: say so rather than guess.
        """
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
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum results to return.")] = 5,
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
        token_budget: Annotated[
            int,
            Field(
                ge=200,
                le=4000,
                description="Approximate size of the answer in tokens. Raise it for broad topics.",
            ),
        ] = 1200,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
    ) -> dict[str, Any]:
        """Budgeted package about one topic. Prefer this over include_messages."""
        services = get_services()
        package = await services.retrieval.for_topic(
            topic, token_budget=token_budget, project=project
        )
        return dump(package)
