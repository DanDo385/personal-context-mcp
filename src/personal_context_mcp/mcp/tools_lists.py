"""List tools. Lists are first-class: never emit a Markdown list instead."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context_mcp.auth import scopes
from personal_context_mcp.auth.principal import current_principal
from personal_context_mcp.mcp.compact import list_item, list_summary
from personal_context_mcp.mcp.result import ToolResult, object_output_schema, text_and_structured
from personal_context_mcp.models import ListItemRecord
from personal_context_mcp.services import get_services

Detail = Literal["compact", "full"]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="list_search",
        tags={"lists", "read"},
        auth=require_scopes(scopes.LISTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def list_search(
        query: Annotated[
            str, Field(description="Words from the list name. Empty returns every list.")
        ] = "",
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum results to return.")] = 20,
    ) -> list[dict]:
        """Find existing lists before creating one. A near-duplicate splits a list."""
        records = await get_services().lists.search(query, limit=limit)
        return [list_summary(record) for record in records]

    @mcp.tool(
        name="list_get",
        tags={"lists", "read"},
        auth=require_scopes(scopes.LISTS_READ),
        annotations={"readOnlyHint": True},
        output_schema=object_output_schema(),
    )
    async def list_get(
        list: Annotated[str, Field(description="List slug or name.")],
        status: Annotated[
            str | None,
            Field(description="open, done, dropped, blocked, or null for all."),
        ] = "open",
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum items to return.")] = 40,
        detail: Annotated[
            Detail,
            Field(description="compact drops nulls and timestamps. full returns every field."),
        ] = "compact",
    ) -> ToolResult:
        """One list and its items. Item ids are stable; use them to complete or move.

        Compact by default. Raise limit only if shown is less than open.
        The text result is one line of counts. Items are in the structured result.
        """
        services = get_services()
        record = await services.lists.get(list)
        items = await services.lists.items(list, status=status, limit=limit)
        shown = len(items)
        open_count = record.open_items
        truncated = open_count > shown if status in (None, "open") else False
        payload = {
            "list": record.slug,
            "name": record.name,
            "open": open_count,
            "shown": shown,
            "truncated": truncated,
            "items": [list_item(item, detail=detail) for item in items],
        }
        if truncated:
            line = f"{record.name}: {shown} of {open_count} open"
        else:
            line = f"{record.name}: {shown} shown, {open_count} open"
        return text_and_structured(line, payload)

    @mcp.tool(
        name="list_create",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
        annotations={"idempotentHint": True},
    )
    async def list_create(
        name: Annotated[str, Field(description="Display name, as the user says it.")],
        description: Annotated[str | None, Field(description="What belongs on it.")] = None,
        list_type: Annotated[
            str,
            Field(description="task, reading, shopping, idea, people or general."),
        ] = "general",
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
    ) -> dict:
        """Create a list only after list_search finds no reasonable home.

        An existing list with the same slug is returned unchanged.
        """
        services = get_services()
        list_id, created = await services.lists.create(
            name=name,
            description=description,
            list_type=list_type,
            project_id=await services.projects.resolve(project),
        )
        await services.audit.record(
            tool="list_create",
            action="create" if created else "read",
            object_type="list",
            object_id=list_id,
            resulting_version={"name": name, "created": created},
        )
        record = await services.lists.get(str(list_id))
        return {**list_summary(record), "created": created}

    @mcp.tool(
        name="list_add_item",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
        annotations={"idempotentHint": True},
    )
    async def list_add_item(
        list: Annotated[str, Field(description="List slug or name. Must already exist.")],
        text: Annotated[str, Field(description="The item, as the user would read it.")],
        notes: Annotated[str | None, Field(description="Extra detail.")] = None,
        priority: Annotated[
            int,
            Field(
                ge=0,
                le=5,
                description="0 (none) to 5 (most urgent). Leave at 0 unless the user gave a priority.",
            ),
        ] = 0,
        due_date: Annotated[
            date | None, Field(description="Due date as YYYY-MM-DD, only if the user gave one.")
        ] = None,
    ) -> dict:
        """Add one item. Matching text updates the existing item instead of duplicating."""
        services = get_services()
        principal = current_principal()
        list_id = await services.lists.require(list)
        item_id, created = await services.lists.add_item(
            list_id=list_id,
            text=text,
            notes=notes,
            priority=priority,
            due_date=due_date,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        await services.audit.record(
            tool="list_add_item",
            action="create" if created else "update",
            object_type="list_item",
            object_id=item_id,
            principal=principal,
            provenance="user_statement",
        )
        items = await services.lists.items(list, status=None, limit=500)
        match = next((i for i in items if i.id == item_id), None)
        if match is None:  # pragma: no cover - the row was just written
            raise RuntimeError("Item disappeared immediately after insert")
        return {**list_item(match), "created": created}

    @mcp.tool(
        name="list_update_item",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
    )
    async def list_update_item(
        item_id: Annotated[UUID, Field(description="Item id from list_get.")],
        text: Annotated[
            str | None, Field(description="Replacement item text. Omit to keep it.")
        ] = None,
        status: Annotated[str | None, Field(description="open, done, dropped or blocked.")] = None,
        priority: Annotated[
            int | None, Field(ge=0, le=5, description="New priority, 0 to 5. Omit to keep it.")
        ] = None,
        due_date: Annotated[
            date | None, Field(description="New due date as YYYY-MM-DD. Omit to keep it.")
        ] = None,
        notes: Annotated[
            str | None, Field(description="Replacement notes. Omit to keep them.")
        ] = None,
    ) -> dict:
        """Change one item. Only supplied fields are altered."""
        services = get_services()
        before = await services.lists.update_item(
            item_id,
            text=text,
            status=status,
            priority=priority,
            due_date=due_date,
            notes=notes,
        )
        await services.audit.record(
            tool="list_update_item",
            action="update",
            object_type="list_item",
            object_id=item_id,
            previous_version=before,
        )
        return {"updated": True, "item_id": str(item_id)}

    @mcp.tool(
        name="list_complete_item",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
        annotations={"idempotentHint": True},
    )
    async def list_complete_item(
        list: Annotated[str, Field(description="List slug or name.")],
        item: Annotated[
            str,
            Field(description="Item id from list_get, or the item's exact text. Prefer the id."),
        ],
    ) -> dict:
        """Mark an item done."""
        services = get_services()
        list_id = await services.lists.require(list)
        item_id = await services.lists.resolve_item(list_id, item)
        if item_id is None:
            return {"completed": False, "reason": "No matching item. Call list_get for ids."}
        before = await services.lists.complete_item(item_id)
        await services.audit.record(
            tool="list_complete_item",
            action="update",
            object_type="list_item",
            object_id=item_id,
            previous_version=before,
        )
        return {"completed": True, "item_id": str(item_id)}

    @mcp.tool(
        name="list_move_item",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
    )
    async def list_move_item(
        item_id: Annotated[UUID, Field(description="Item id from list_get.")],
        target_list: Annotated[str, Field(description="Destination list slug or name.")],
    ) -> dict:
        """Move an item between lists."""
        services = get_services()
        target_id = await services.lists.require(target_list)
        await services.lists.move_item(item_id, target_list_id=target_id)
        await services.audit.record(
            tool="list_move_item",
            action="update",
            object_type="list_item",
            object_id=item_id,
            resulting_version={"target_list": target_list},
        )
        return {"moved": True, "item_id": str(item_id), "target_list": target_list}

    @mcp.tool(
        name="list_archive_item",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
    )
    async def list_archive_item(
        item_id: Annotated[UUID, Field(description="Item id from list_get.")],
    ) -> dict:
        """Drop an item without deleting it. The row and its history remain."""
        services = get_services()
        before = await services.lists.update_item(item_id, status="dropped")
        await services.audit.record(
            tool="list_archive_item",
            action="update",
            object_type="list_item",
            object_id=item_id,
            previous_version=before,
        )
        return {"archived": True, "item_id": str(item_id)}

    @mcp.tool(
        name="list_recent",
        tags={"lists", "read"},
        auth=require_scopes(scopes.LISTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def list_recent(
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results to return.")] = 15,
    ) -> list[dict]:
        """Recently changed items across every list."""
        items = await get_services().lists.recent_items(limit=limit)
        return [_with_list(item) for item in items]

    @mcp.tool(
        name="list_find_item",
        tags={"lists", "read"},
        auth=require_scopes(scopes.LISTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def list_find_item(
        query: Annotated[str, Field(description="Words from the item.")],
        limit: Annotated[int, Field(ge=1, le=30, description="Maximum results to return.")] = 10,
        status: Annotated[str | None, Field(description="Filter by status.")] = None,
        include_archived: Annotated[
            bool, Field(description="Also search items in archived lists.")
        ] = False,
    ) -> list[dict]:
        """Search items across every list, when you know the item but not the list.

        Archived lists are skipped unless include_archived is set; their items
        are otherwise invisible to this search.
        """
        items = await get_services().lists.search_items(
            query, limit=limit, status=status, include_archived=include_archived
        )
        return [_with_list(item) for item in items]


def _with_list(item: ListItemRecord) -> dict:
    return {**list_item(item), "list": item.list_slug}
