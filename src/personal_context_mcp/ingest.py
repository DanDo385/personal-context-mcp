"""Browser extension ingest endpoint and device token auth.

POST /api/v1/ingest/conversation
    Idempotent upsert of a conversation with messages.
    Auth: Bearer <device-token>

POST /api/v1/auth/register-device
    Register a browser and receive a device token.

DELETE /api/v1/auth/devices/{device_id}
    Revoke a device token.
"""

from __future__ import annotations

import secrets
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from personal_context_mcp.config import Settings
from personal_context_mcp.importers.base import parse_timestamp
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.services import get_services

logger = get_logger(__name__)


def _bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header, if present."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def _validate_device_token(db, token: str) -> dict[str, Any] | None:
    """Validate a device token. Returns device record or None."""
    row = await db.fetchrow(
        """
        SELECT id, device_id, device_name
        FROM device_tokens
        WHERE token = $1 AND revoked_at IS NULL
        """,
        token,
    )
    if row is None:
        return None
    # Update last_used_at.
    await db.execute(
        "UPDATE device_tokens SET last_used_at = now() WHERE id = $1",
        row["id"],
    )
    return dict(row)


def _make_token() -> str:
    """Generate a cryptographically random device token."""
    return f"pcd_{secrets.token_urlsafe(32)}"


def build_ingest_routes(settings: Settings) -> list:
    """Build ingest API routes as closures over settings."""

    # ---- Auth routes ----

    async def register_device(request: Request) -> JSONResponse:
        """Register a browser and return a device token."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        device_name = body.get("device_name", "unknown") if isinstance(body, dict) else "unknown"
        device_id = f"dev_{secrets.token_urlsafe(12)}"
        token = _make_token()

        try:
            db = get_services().db
            await db.execute(
                """
                INSERT INTO device_tokens (device_id, token, device_name)
                VALUES ($1, $2, $3)
                """,
                device_id,
                token,
                device_name,
            )
        except Exception as exc:
            logger.exception("device registration failed")
            return JSONResponse(
                {"error": "registration failed", "detail": str(exc)},
                status_code=500,
            )

        logger.info(
            "device registered",
            extra={"device_id": device_id, "device_name": device_name},
        )

        return JSONResponse(
            {
                "device_id": device_id,
                "device_token": token,
                "device_name": device_name,
            }
        )

    async def revoke_device(request: Request) -> JSONResponse:
        """Revoke a device token by device_id."""
        device_id = request.path_params.get("device_id", "")
        if not device_id:
            return JSONResponse({"error": "missing device_id"}, status_code=400)

        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        device = await _validate_device_token(get_services().db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Only allow self-revocation or admin (future).
        if device["device_id"] != device_id:
            return JSONResponse({"error": "can only revoke own device"}, status_code=403)

        await get_services().db.execute(
            "UPDATE device_tokens SET revoked_at = now() WHERE device_id = $1",
            device_id,
        )

        logger.info("device revoked", extra={"device_id": device_id})
        return JSONResponse({"ok": True, "device_id": device_id})

    # ---- Ingest route ----

    async def ingest_conversation(request: Request) -> JSONResponse:
        """Idempotent upsert of a conversation with messages."""
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        services = get_services()
        db = services.db
        device = await _validate_device_token(db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        if not isinstance(body, dict):
            return JSONResponse({"error": "expected object"}, status_code=400)

        provider = body.get("provider")
        provider_conversation_id = body.get("provider_conversation_id")
        if not provider or not provider_conversation_id:
            return JSONResponse(
                {"error": "provider and provider_conversation_id are required"},
                status_code=400,
            )

        title = body.get("title")
        model = body.get("model")
        updated_at = parse_timestamp(body.get("updated_at"))
        messages = body.get("messages", [])
        attachments = body.get("attachments", [])
        if not isinstance(messages, list) or not isinstance(attachments, list):
            return JSONResponse(
                {"error": "messages and attachments must be lists"}, status_code=400
            )
        if not isinstance(provider, str) or not isinstance(provider_conversation_id, str):
            return JSONResponse(
                {"error": "provider and provider_conversation_id must be strings"}, status_code=400
            )
        if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM providers WHERE id = $1)", provider):
            return JSONResponse({"error": "unknown provider"}, status_code=400)
        normalized = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
                return JSONResponse(
                    {"error": "each message must contain text content"}, status_code=400
                )
            if not message.get("content", "").strip():
                continue
            normalized.append(
                {
                    **message,
                    "content": message["content"].replace("\x00", ""),
                    "created_at": parse_timestamp(message.get("created_at")),
                    "provenance": "imported_metadata",
                }
            )

        # Use the canonical conversation/message writers and their dedup keys.
        async with db.transaction() as conn:
            conv_id, created = await services.sessions.ensure_conversation(
                provider=provider,
                provider_conversation_id=provider_conversation_id,
                title=title,
                model=model,
                imported=True,
                started_at=parse_timestamp(body.get("created_at")),
                conn=conn,
            )
            inserted = await services.sessions.add_messages(
                conv_id, normalized, provider=provider, conn=conn
            )
            skipped = len(normalized) - inserted
            await conn.execute(
                "UPDATE conversations SET updated_at = coalesce($2::timestamptz, updated_at), metadata = metadata || $3::jsonb WHERE id = $1",
                conv_id,
                updated_at,
                {"_attachments": attachments} if attachments else {},
            )

        status = "created" if created else "updated"
        if created:
            logger.info(
                "ingest: conversation created",
                extra={"provider": provider, "id": str(conv_id), "messages": inserted},
            )
        else:
            logger.info(
                "ingest: conversation updated",
                extra={
                    "provider": provider,
                    "id": str(conv_id),
                    "messages_inserted": inserted,
                    "messages_skipped": skipped,
                },
            )

        return JSONResponse(
            {
                "conversation_id": str(conv_id),
                "status": status,
                "messages_inserted": inserted,
                "messages_skipped": skipped,
            }
        )

    # ---- Handoff route (for side panel) ----

    async def handoff(request: Request) -> JSONResponse:
        """Return the current project handoff for the side panel."""
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        services = get_services()
        db = services.db
        device = await _validate_device_token(db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Return the most recent active project handoff.
        # Simplified: returns the last 3 session summaries.
        rows = await db.fetch(
            """
            SELECT ss.title, ss.summary, ss.open_questions, ss.important_context,
                   c.provider, c.updated_at
            FROM session_summaries ss
            JOIN conversations c ON c.id = ss.conversation_id
            WHERE ss.classification_status IN ('classified','needs_review')
            ORDER BY c.updated_at DESC
            LIMIT 3
            """
        )

        if not rows:
            return JSONResponse({"handoff": "No recent session handoffs available."})

        parts = []
        for row in rows:
            parts.append(f"## {row['title'] or 'Untitled'} ({row['provider']})")
            parts.append(row["summary"] or "")
            open_qs = row.get("open_questions") or []
            if isinstance(open_qs, list) and open_qs:
                parts.append("\nOpen questions:")
                for q in open_qs:
                    parts.append(f"- {q}")
            parts.append("")

        return JSONResponse({"handoff": "\n".join(parts)})

    from starlette.routing import Route

    return [
        Route("/api/v1/auth/register-device", register_device, methods=["POST"]),
        Route("/api/v1/auth/devices/{device_id}", revoke_device, methods=["DELETE"]),
        Route("/api/v1/ingest/conversation", ingest_conversation, methods=["POST"]),
        Route("/api/v1/handoff", handoff, methods=["GET"]),
    ]
