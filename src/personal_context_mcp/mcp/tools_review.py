"""Human review queue tools.

Deliberately no generic delete. Dismissing a review item closes the question;
it never destroys the object the question was about.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context_mcp.auth import scopes
from personal_context_mcp.services import get_services


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="review_list",
        tags={"review", "read"},
        auth=require_scopes(scopes.REVIEW),
        annotations={"readOnlyHint": True},
    )
    async def review_list(
        limit: Annotated[
            int, Field(ge=1, le=100, description="Maximum review items to return.")
        ] = 25,
        object_type: Annotated[
            str | None,
            Field(description="conversation, artifact, note or list_candidate."),
        ] = None,
    ) -> dict:
        """Items whose routing was too uncertain to decide automatically.

        Least confident first. Use during a daily review to clear ambiguity the
        classifier correctly declined to guess at.
        """
        services = get_services()
        return {
            "counts": await services.review.counts(),
            "items": await services.review.list_open(limit=limit, object_type=object_type),
        }

    @mcp.tool(
        name="review_get",
        tags={"review", "read"},
        auth=require_scopes(scopes.REVIEW),
        annotations={"readOnlyHint": True},
    )
    async def review_get(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
    ) -> dict:
        """One review item with the classifier's full answer set and probabilities."""
        return await get_services().review.get(review_id)

    @mcp.tool(
        name="review_approve",
        tags={"review", "write"},
        auth=require_scopes(scopes.REVIEW),
    )
    async def review_approve(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
        note: Annotated[str | None, Field(description="Why, for the audit trail.")] = None,
    ) -> dict:
        """Accept the suggested routing and close the item."""
        services = get_services()
        result = await services.review.resolve(
            review_id, status="approved", resolution={"note": note}
        )
        await services.audit.record(
            tool="review_approve",
            action="approve",
            object_type=result["object_type"],
            object_id=result["object_id"],
            approval_status="user_approved",
        )
        return result

    @mcp.tool(
        name="review_reclassify",
        tags={"review", "write"},
        auth=require_scopes(scopes.REVIEW),
    )
    async def review_reclassify(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
        project: Annotated[str | None, Field(description="Correct project slug or name.")] = None,
        destination: Annotated[
            str | None,
            Field(description="Correct vault section: project, research, wiki, etc."),
        ] = None,
        note: Annotated[str | None, Field(description="Why the classifier was wrong.")] = None,
    ) -> dict:
        """Override the classifier's routing.

        The override is recorded against the original classification event,
        which is how the classifier's accuracy gets measured over time.
        """
        services = get_services()
        item = await services.review.get(review_id)
        resolution = {"project": project, "destination": destination, "note": note}

        if project and item["object_type"] == "artifact":
            project_id = await services.projects.require(project)
            await services.artifacts.attach_to_project(item["object_id"], project_id)
        elif project and item["object_type"] == "conversation":
            project_id = await services.projects.require(project)
            await services.db.execute(
                "UPDATE conversations SET project_id = $2 WHERE id = $1",
                item["object_id"],
                project_id,
            )

        result = await services.review.resolve(
            review_id,
            status="reclassified",
            resolution=resolution,
            overridden=True,
            override_note=note,
        )
        await services.audit.record(
            tool="review_reclassify",
            action="update",
            object_type=result["object_type"],
            object_id=result["object_id"],
            approval_status="user_approved",
            resulting_version=resolution,
            classification_event_id=item.get("classification_event_id"),
        )
        return result

    @mcp.tool(
        name="review_dismiss",
        tags={"review", "write"},
        auth=require_scopes(scopes.REVIEW),
    )
    async def review_dismiss(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
        note: Annotated[str | None, Field(description="Why it needs no action.")] = None,
    ) -> dict:
        """Close a review item without changing anything.

        The underlying object is untouched - this only says the question has
        been considered.
        """
        services = get_services()
        return await services.review.resolve(
            review_id, status="dismissed", resolution={"note": note}
        )

    @mcp.tool(
        name="sync_status",
        tags={"review", "read"},
        auth=require_scopes(scopes.NOTES_READ),
        annotations={"readOnlyHint": True},
    )
    async def sync_status() -> dict:
        """State of the Obsidian sync queue.

        Use to answer "did that get written to my vault yet" honestly: a
        pending count above zero means the local worker has not run, or is not
        running at all.
        """
        services = get_services()
        rows = await services.db.fetch(
            """
            SELECT status, count(*) AS n, max(updated_at) AS latest
            FROM sync_jobs GROUP BY status
            """
        )
        conflicts = await services.db.fetch(
            """
            SELECT vault_path, last_error, updated_at FROM sync_jobs
            WHERE status = 'conflict' ORDER BY updated_at DESC LIMIT 10
            """
        )
        last_worker = await services.db.fetchval(
            "SELECT max(created_at) FROM sync_events WHERE worker_id IS NOT NULL"
        )
        return {
            "by_status": {r["status"]: int(r["n"]) for r in rows},
            "conflicts": [dict(c) for c in conflicts],
            "last_worker_activity": last_worker,
            "note": (
                "Writes land only when the local obsidian-sync worker polls. If "
                "last_worker_activity is stale, the worker is not running and "
                "nothing has reached the vault."
            ),
        }
