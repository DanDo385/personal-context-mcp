"""HTTP surface for the local Obsidian sync worker.

Separate from MCP on purpose. The worker is our own code running on the user's
machine, not a model: it authenticates with a shared secret over an
**outbound** connection and speaks a small, fixed job protocol. Nothing here
takes a path from the caller - the worker is told which path to write, and it
validates that path again locally before touching the disk.
"""

from __future__ import annotations

import hmac
import json
from typing import Any
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from personal_context_mcp.config import Settings
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.obsidian.render import render_artifact_note, render_list, render_note
from personal_context_mcp.services import Services

logger = get_logger(__name__)


def _unauthorized() -> JSONResponse:
    # No detail: an unauthenticated caller learns nothing about this service.
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def authorize(request: Request, settings: Settings) -> bool:
    expected = settings.worker_api_key.get_secret_value()
    if not expected:
        return False
    supplied = request.headers.get("x-worker-key", "")
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def lease_jobs(request: Request, services: Services) -> Response:
    if not authorize(request, services.settings):
        return _unauthorized()
    body = _json_body(await request.body())
    worker_id = str(body.get("worker_id") or "unknown")
    limit = min(int(body.get("limit") or 10), 50)

    jobs = await services.sync_queue.lease(worker_id=worker_id, limit=limit)

    deliverable: list[dict[str, Any]] = []
    for job in jobs:
        rendered = await _render_job(services, job)
        if rendered is not None:
            deliverable.append(rendered)
            continue
        # A job we cannot render was still leased. Release it explicitly with
        # a reason, rather than leaving a row that is invisible to the worker
        # and silently blocked until its lease expires.
        await services.sync_queue.fail(
            job["id"],
            worker_id=worker_id,
            error="server could not render this job; its target may have been deleted",
        )
        logger.warning(
            "released an unrenderable sync job",
            extra={"job_type": job["job_type"], "vault_path": job["vault_path"]},
        )

    return JSONResponse({"jobs": deliverable}, status_code=200)


async def _render_job(services: Services, job: dict[str, Any]) -> dict[str, Any] | None:
    """Turn a queued job into exactly the bytes the worker should write.

    Rendering happens here, not on the laptop, so the worker stays a dumb,
    auditable file writer with no knowledge of the schema.
    """
    payload = {
        "id": str(job["id"]),
        "job_type": job["job_type"],
        "vault_path": job["vault_path"],
        "expected_sha256": job["expected_sha256"],
        "attempts": job["attempts"],
    }

    try:
        if job["job_type"] == "write_note":
            row = await services.db.fetchrow(
                """
                SELECT n.*, p.slug AS project_slug
                FROM notes n LEFT JOIN projects p ON p.id = n.project_id
                WHERE n.id = $1
                """,
                job["object_id"],
            )
            if row is None:
                return None
            payload["content"] = render_note(dict(row))
            payload["content_type"] = "text/markdown"

        elif job["job_type"] == "write_list":
            list_row = await services.db.fetchrow(
                """
                SELECT l.*, p.slug AS project_slug
                FROM lists l LEFT JOIN projects p ON p.id = l.project_id
                WHERE l.id = $1
                """,
                job["object_id"],
            )
            if list_row is None:
                return None
            items = await services.db.fetch(
                """
                SELECT id, text, status, priority, due_date, notes
                FROM list_items WHERE list_id = $1
                ORDER BY status = 'done', priority DESC, position
                """,
                job["object_id"],
            )
            payload["content"] = render_list(dict(list_row), [dict(i) for i in items])
            payload["content_type"] = "text/markdown"

        elif job["job_type"] == "write_artifact_note":
            artifact = await services.db.fetchrow(
                """
                SELECT a.*, p.slug AS project_slug
                FROM artifacts a LEFT JOIN projects p ON p.id = a.project_id
                WHERE a.id = $1
                """,
                job["object_id"],
            )
            if artifact is None:
                return None
            files = await services.db.fetch(
                "SELECT * FROM artifact_files WHERE artifact_id = $1 ORDER BY relative_path",
                job["object_id"],
            )
            payload["content"] = render_artifact_note(
                dict(artifact),
                [dict(f) for f in files],
                download_hint=(
                    "Ask any connected assistant for "
                    f"`artifact_signed_download` on artifact `{artifact['slug']}` "
                    "to get short-lived links to these files."
                ),
            )
            payload["content_type"] = "text/markdown"

        elif job["job_type"] == "mirror_artifact_files":
            object_key = job["payload"].get("object_key")
            if not object_key:
                return None
            # Signed URL rather than the bytes: the worker fetches directly,
            # keeping large files out of this response.
            payload["download_url"] = await services.store.signed_download_url(
                object_key, expires_in=services.settings.signed_url_ttl_seconds
            )
            payload["expected_content_sha256"] = job["content_sha256"]
            payload["content_type"] = "application/octet-stream"

        else:
            logger.warning("unknown job type", extra={"job_type": job["job_type"]})
            return None

    except Exception:
        logger.exception("failed to render sync job", extra={"job_id": str(job["id"])})
        return None

    return payload


async def ack_job(request: Request, services: Services) -> Response:
    if not authorize(request, services.settings):
        return _unauthorized()
    body = _json_body(await request.body())
    try:
        job_id = UUID(str(body["job_id"]))
    except (KeyError, ValueError):
        return JSONResponse({"error": "job_id is required"}, status_code=400)

    worker_id = str(body.get("worker_id") or "unknown")
    status = str(body.get("status") or "done")

    if status == "done":
        await services.sync_queue.complete(
            job_id, worker_id=worker_id, written_sha256=body.get("sha256")
        )
    elif status == "conflict":
        await services.sync_queue.fail(
            job_id,
            worker_id=worker_id,
            error=str(body.get("error") or "file changed on disk"),
            conflict=True,
        )
    else:
        await services.sync_queue.fail(
            job_id, worker_id=worker_id, error=str(body.get("error") or "unknown error")
        )
    return JSONResponse({"ok": True})


async def push_edits(request: Request, services: Services) -> Response:
    """Human edits made in the vault, pushed back up by the worker."""
    if not authorize(request, services.settings):
        return _unauthorized()
    body = _json_body(await request.body())
    worker_id = str(body.get("worker_id") or "unknown")

    applied: list[dict[str, Any]] = []
    for edit in body.get("edits") or []:
        kind = edit.get("kind")
        try:
            if kind == "note":
                result = await services.notes.apply_vault_edit(
                    vault_path=edit["vault_path"], content=edit["content"]
                )
                applied.append({"vault_path": edit["vault_path"], **result})
            elif kind == "list_items":
                applied.append(await _apply_list_edits(services, edit))
            await services.sync_queue.record_inbound(
                vault_path=str(edit.get("vault_path")),
                sha256=str(edit.get("sha256") or ""),
                worker_id=worker_id,
                detail=f"human edit ({kind})",
            )
        except Exception as exc:
            logger.exception("failed to apply vault edit")
            applied.append({"vault_path": edit.get("vault_path"), "error": str(exc)})

    return JSONResponse({"applied": applied})


async def _apply_list_edits(services: Services, edit: dict[str, Any]) -> dict[str, Any]:
    """Apply checkbox state parsed out of a rendered list file.

    Identity comes from the `pc:id` marker only. An item whose marker is
    missing is reported, never matched by text - guessing here would tick the
    wrong thing.
    """
    changed = 0
    unmatched: list[str] = []
    for item in edit.get("items") or []:
        raw_id = item.get("id")
        if not raw_id:
            unmatched.append(str(item.get("text", ""))[:80])
            continue
        try:
            item_id = UUID(str(raw_id))
        except ValueError:
            unmatched.append(str(raw_id))
            continue

        target_status = "done" if item.get("checked") else "open"
        result = await services.db.execute(
            """
            UPDATE list_items
            SET status = $2,
                completed_at = CASE WHEN $2 = 'done' THEN coalesce(completed_at, now())
                                    ELSE NULL END,
                provenance = 'user_statement'::pc_provenance
            WHERE id = $1 AND status <> $2
            """,
            item_id,
            target_status,
        )
        if result.rsplit(" ", 1)[-1] != "0":
            changed += 1
            await services.audit.record(
                tool="obsidian_sync",
                action="update",
                object_type="list_item",
                object_id=item_id,
                resulting_version={"status": target_status},
                provenance="user_statement",
                approval_status="user_approved",
                metadata={"source": "vault_edit", "vault_path": edit.get("vault_path")},
            )
    return {
        "vault_path": edit.get("vault_path"),
        "changed": changed,
        "unmatched": unmatched,
    }
