"""Inbound telemetry for the HTTP edge.

One structured INFO line per HTTP request: method, path, client User-Agent,
authentication *fingerprint*, and — for JSON-RPC calls — the protocol method,
tool name and parameter keys.

Two deliberate omissions, both documented here so a future reader does not
"fix" them back:

* The raw ``Authorization`` value is never logged, only ``<scheme>:<sha256
  prefix>``. Logging bearer tokens would turn the log stream into a
  credential store; a fingerprint is enough to correlate a client's requests
  and to confirm which token a remote app actually sent.
* The query string is never logged. Artifact signed URLs carry their
  signature in ``?sig=``, and echoing it would extend the URL's lifetime to
  the log retention period.

Raw JSON-RPC bodies are logged only when ``PC_LOG_RPC_BODIES`` is set, which
is an explicit debugging act: bodies carry user content. The always-on line
carries the method, tool name, parameter names and body size instead, which
is sufficient to diagnose handshake failures (wrong path, missing auth,
unknown method, rejected params) without storing user data.

Written against the raw ASGI interface — like
``SecurityHeadersMiddleware`` — so streamed MCP responses are never buffered.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from personal_context_mcp.logging_setup import get_logger

logger = get_logger(__name__)

#: Request bodies larger than this are counted but not retained. JSON-RPC
#: requests are small; artifact uploads go to object storage, not through here.
_MAX_BODY_BYTES = 256 * 1024


def token_fingerprint(authorization: str) -> str:
    """``<scheme>:<sha256-16>`` for an Authorization header value, or ``-``.

    The digest confirms *which* token a client sent without recording a value
    that could be replayed.
    """
    scheme, _, credentials = authorization.strip().partition(" ")
    if not credentials:
        return "-"
    digest = hashlib.sha256(credentials.encode("utf-8")).hexdigest()[:16]
    return f"{scheme or 'bearer'}:{digest}"


def summarise_rpc(body: bytes) -> dict[str, Any]:
    """Structural summary of a JSON-RPC body: no user content, just shape.

    Returns ``{}`` when the body is not a JSON-RPC object or batch. For
    ``tools/call`` the summary names the tool and its parameter keys so a
    missing/renamed argument is visible without logging the values.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {}
    candidates = payload if isinstance(payload, list) else [payload]
    methods: list[str] = []
    tool: str | None = None
    param_keys: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        method = item.get("method")
        if isinstance(method, str):
            methods.append(method)
        if method == "tools/call" and tool is None:
            params = item.get("params") or {}
            if isinstance(params, dict):
                name = params.get("name")
                if isinstance(name, str):
                    tool = name
                arguments = params.get("arguments") or {}
                if isinstance(arguments, dict):
                    param_keys = sorted(str(key) for key in arguments)
    if not methods:
        return {}
    summary: dict[str, Any] = {"rpc_methods": methods}
    if tool is not None:
        summary["rpc_tool"] = tool
        summary["rpc_param_keys"] = param_keys
    return summary


class McpTelemetryMiddleware:
    """Pure-ASGI request logging. See the module docstring for the redaction rules."""

    def __init__(self, app: Any, *, log_bodies: bool = False) -> None:
        self.app = app
        self.log_bodies = log_bodies

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        user_agent = headers.get(b"user-agent", b"").decode("latin-1", "replace")[:300]
        authorization = headers.get(b"authorization", b"").decode("latin-1", "replace")

        chunks: list[bytes] = []
        retained = 0

        async def receive_wrapper() -> Any:
            nonlocal retained
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if chunk and retained < _MAX_BODY_BYTES:
                    chunks.append(chunk)
                    retained += len(chunk)
            return message

        status: dict[str, int] = {}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start" and "status" not in status:
                status["status"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            body = b"".join(chunks)
            extra: dict[str, Any] = {
                "http_method": scope.get("method", "-"),
                "http_path": scope.get("path", "-"),
                "user_agent": user_agent or "-",
                "auth": token_fingerprint(authorization),
                "request_bytes": retained,
            }
            if scope.get("method") == "POST" and body.startswith((b"{", b"[")):
                extra.update(summarise_rpc(body))
                if self.log_bodies:
                    extra["rpc_body"] = body[:_MAX_BODY_BYTES].decode("utf-8", "replace")
            if "status" in status:
                extra["http_status"] = status["status"]
            logger.info("http request", extra=extra)
