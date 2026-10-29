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

import contextlib
import secrets
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from personal_context_mcp.config import Settings
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.services import get_services
from personal_context_mcp.util import sha256_text, utcnow

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

        # Only allow self-revocation.
        if device["device_id"] != device_id:
            return JSONResponse({"error": "can only revoke own device"}, status_code=403)

        await get_services().db.execute(
            "UPDATE device_tokens SET revoked_at = now() WHERE device_id = $1",
            device_id,
        )

        logger.info("device revoked", extra={"device_id": device_id})
        return JSONResponse({"ok": True, "device_id": device_id})

    async def pair_device(request: Request) -> JSONResponse:
        """Pair a browser using a one-time code from the server CLI."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        if not isinstance(body, dict):
            return JSONResponse({"error": "expected object"}, status_code=400)

        pairing_code = (body.get("pairing_code") or "").strip().upper()
        device_name = body.get("device_name", "unknown")

        if not pairing_code:
            return JSONResponse({"error": "pairing_code is required"}, status_code=400)

        db = get_services().db

        # Look up the code — must exist, be unused, and not expired.
        row = await db.fetchrow(
            """
            SELECT id, device_name
            FROM pairing_codes
            WHERE code = $1 AND used_at IS NULL AND expires_at > now()
            """,
            pairing_code,
        )

        if row is None:
            return JSONResponse({"error": "invalid or expired pairing code"}, status_code=403)

        # Create device token.
        device_id = f"dev_{secrets.token_urlsafe(12)}"
        token = _make_token()

        await db.execute(
            """
            INSERT INTO device_tokens (device_id, token, device_name)
            VALUES ($1, $2, $3)
            """,
            device_id,
            token,
            device_name or row["device_name"] or "unknown",
        )

        # Mark code as used.
        await db.execute(
            "UPDATE pairing_codes SET used_at = now(), used_by_device_id = $1 WHERE id = $2",
            device_id,
            row["id"],
        )

        logger.info(
            "device paired",
            extra={"device_id": device_id, "device_name": device_name},
        )

        return JSONResponse(
            {
                "device_id": device_id,
                "device_token": token,
                "device_name": device_name,
            }
        )

    # ---- Ingest route ----

    async def ingest_conversation(request: Request) -> JSONResponse:
        """Idempotent upsert of a conversation with messages."""
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        db = get_services().db
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
        updated_at_raw = body.get("updated_at")
        messages = body.get("messages", [])

        # Convert ISO strings to datetime for asyncpg.
        updated_at = None
        if updated_at_raw:
            with contextlib.suppress(ValueError, TypeError):
                updated_at = datetime.fromisoformat(updated_at_raw.replace("Z", "+00:00"))

        # Attachments stored as metadata for now (v1 references only).
        attachments = body.get("attachments", [])

        if not isinstance(messages, list):
            return JSONResponse({"error": "messages must be a list"}, status_code=400)

        # Use the same synthetic ID logic as ensure_conversation if needed.
        key_seed = f"{provider}:{title or ''}:{utcnow():%Y-%m-%d}"
        external_id = provider_conversation_id or (f"synthetic:{sha256_text(key_seed)[:32]}")

        # Upsert conversation — mirrors sessions/service.py ensure_conversation.
        row = await db.fetchrow(
            """
            INSERT INTO conversations (
                provider, provider_conversation_id, title, model,
                imported, import_key
            ) VALUES ($1,$2,$3,$4,true,$5)
            ON CONFLICT (provider, provider_conversation_id)
                WHERE provider_conversation_id IS NOT NULL
            DO UPDATE SET
                title = coalesce(EXCLUDED.title, conversations.title),
                model = coalesce(EXCLUDED.model, conversations.model),
                updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            provider,
            external_id,
            title,
            model,
            f"{provider}:{external_id}",
        )
        if row is None:
            return JSONResponse({"error": "upsert failed"}, status_code=500)
        conv_id = row["id"]
        created = bool(row["created"])

        # Upsert messages with the provided IDs.
        inserted = 0
        skipped = 0
        for msg in messages:
            if not msg.get("content"):
                continue
            provider_msg_id = msg.get("provider_message_id")
            role = msg.get("role", "user")
            content = msg["content"]
            msg_model = msg.get("model")
            msg_created_at = msg.get("created_at")
            content_hash = sha256_text(content)

            # Convert ISO string to datetime for asyncpg.
            msg_dt = None
            if msg_created_at:
                with contextlib.suppress(ValueError, TypeError):
                    msg_dt = datetime.fromisoformat(msg_created_at.replace("Z", "+00:00"))

            result = await db.fetchval(
                """
                INSERT INTO messages (
                    conversation_id, provider, provider_message_id,
                    model, role, content, content_sha256,
                    provenance, sequence, created_at, metadata
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    'imported_metadata'::pc_provenance,
                    (SELECT coalesce(max(sequence), -1) + 1
                     FROM messages WHERE conversation_id = $1),
                    coalesce($8::timestamptz, now()),
                    '{}'::jsonb
                )
                ON CONFLICT (conversation_id, provider_message_id)
                    WHERE provider_message_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """,
                conv_id,
                provider,
                provider_msg_id,
                msg_model,
                role,
                content,
                content_hash,
                msg_dt,
            )
            if result:
                inserted += 1
            else:
                skipped += 1

        # Update conversation timestamps.
        await db.execute(
            """
            UPDATE conversations
            SET updated_at = coalesce($2::timestamptz, updated_at),
                metadata = metadata || $3::jsonb
            WHERE id = $1
            """,
            conv_id,
            updated_at,
            {"_attachments": attachments} if attachments else "{}",
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

        db = get_services().db
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
            WHERE ss.classification_status = 'classified'
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
        Route("/api/v1/auth/pair-device", pair_device, methods=["POST"]),
        Route("/api/v1/auth/devices/{device_id}", revoke_device, methods=["DELETE"]),
        Route("/api/v1/ingest/conversation", ingest_conversation, methods=["POST"]),
        Route("/api/v1/handoff", handoff, methods=["GET"]),
    ]
