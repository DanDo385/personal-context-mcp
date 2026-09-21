"""List tools. Lists are first-class: never emit a Markdown list instead."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context.auth import scopes
from personal_context.auth.principal import current_principal
from personal_context.models import ListItemRecord, ListRecord
from personal_context.services import get_services


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
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[ListRecord]:
        """Find the user's existing lists.

        ALWAYS call this before list_create. The user keeps many lists and they
        rarely have the exact name used in conversation - "my reading list" may
        be stored as "Books to read". Creating a near-duplicate splits a list
        in two and is hard to undo.
        """
        return await get_services().lists.search(query, limit=limit)

    @mcp.tool(
        name="list_get",
        tags={"lists", "read"},
        auth=require_scopes(scopes.LISTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def list_get(
        list: Annotated[str, Field(description="List slug or name.")],
        status: Annotated[
            str | None,
            Field(description="open, done, dropped, blocked, or null for all."),
        ] = "open",
        limit: Annotated[int, Field(ge=1, le=500)] = 200,
    ) -> dict:
        """One list with its items. Item ids returned here are stable - use
        them with list_complete_item and list_move_item rather than matching
        on text."""
        services = get_services()
        record = await services.lists.get(list)
        items = await services.lists.items(list, status=status, limit=limit)
        return {
            "list": record.model_dump(mode="json"),
            "items": [i.model_dump(mode="json") for i in items],
        }

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
    ) -> ListRecord:
        """Create a new list. Call list_search first - only create when nothing
        existing is a reasonable home. An existing list with the same slug is
        returned unchanged rather than duplicated."""
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
        return await services.lists.get(str(list_id))

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
        priority: Annotated[int, Field(ge=0, le=5)] = 0,
        due_date: date | None = None,
    ) -> ListItemRecord:
        """Add one item to an existing list.

        An item whose text matches one already on the list updates it instead
        of adding a second copy.
        """
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
        return match

    @mcp.tool(
        name="list_update_item",
        tags={"lists", "write"},
        auth=require_scopes(scopes.LISTS_WRITE),
    )
    async def list_update_item(
        item_id: Annotated[UUID, Field(description="Item id from list_get.")],
        text: str | None = None,
        status: Annotated[str | None, Field(description="open, done, dropped or blocked.")] = None,
        priority: Annotated[int | None, Field(ge=0, le=5)] = None,
        due_date: date | None = None,
        notes: str | None = None,
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
        return {"item_id": str(item_id), "previous": before}

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
            Field(
                description=(
                    "Item id from list_get, or the item's exact text. Prefer the "
                    "id - matching on text can hit the wrong item."
                )
            ),
        ],
    ) -> dict:
        """Mark an item done."""
        services = get_services()
        list_id = await services.lists.require(list)
        item_id = await services.lists.resolve_item(list_id, item)
        if item_id is None:
            return {
                "completed": False,
                "reason": f"No item on {list!r} matches {item!r}. Call list_get to see ids.",
            }
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
        """Move an item between lists, e.g. from 'someday' to 'active'."""
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
    async def list_recent(limit: Annotated[int, Field(ge=1, le=100)] = 20) -> list[ListItemRecord]:
        """Recently changed items across every list."""
        return await get_services().lists.recent_items(limit=limit)

    @mcp.tool(
        name="list_find_item",
        tags={"lists", "read"},
        auth=require_scopes(scopes.LISTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def list_find_item(
        query: Annotated[str, Field(description="Words from the item.")],
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        status: Annotated[str | None, Field(description="Filter by status.")] = None,
    ) -> list[ListItemRecord]:
        """Search items across every list, when you know the item but not the list."""
        return await get_services().lists.search_items(query, limit=limit, status=status)
