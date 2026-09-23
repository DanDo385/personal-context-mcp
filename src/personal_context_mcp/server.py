"""Server assembly.

Builds the FastMCP server, mounts it in a Starlette app alongside the worker
API and health routes, and owns the lifespan that opens and closes the
database pool and every service.

Route map:

    /                      service identity only; no personal data
    /healthz               liveness
    /readyz                readiness (checks the database)
    /mcp[/<server-id>]     the MCP endpoint, authenticated
    /.well-known/*         OAuth metadata, added by the auth provider
    /worker/*              local sync worker, shared-secret authenticated
    /artifacts/{up,down}   local storage backend only
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route

from personal_context_mcp import worker_api
from personal_context_mcp.auth.kv import PostgresKeyValue
from personal_context_mcp.auth.provider import build_auth_provider
from personal_context_mcp.auth.ratelimit import RateLimitMiddleware
from personal_context_mcp.config import Settings, get_settings
from personal_context_mcp.db.pool import Database, set_database
from personal_context_mcp.logging_setup import (
    configure_logging,
    get_logger,
    request_id_var,
)
from personal_context_mcp.mcp import register_all
from personal_context_mcp.services import build_services, get_services, set_services
from personal_context_mcp.storage import build_object_store
from personal_context_mcp.storage.local import LocalObjectStore

logger = get_logger(__name__)

SERVER_INSTRUCTIONS = """\
Personal Context is this user's canonical memory, shared across ChatGPT, \
Claude, Gemini and Grok.

Retrieve before answering (context_bootstrap) whenever the request could \
depend on earlier conversations, projects, decisions, preferences, research, \
tasks, lists, people or artifacts - and always when the user refers to \
something previously discussed. Skip retrieval for trivial self-contained \
questions.

Checkpoint durable state (session_checkpoint) when something is settled: a \
decision, a confirmed fact, a task, a list change, a project update, an \
unresolved question. Not conversational noise.

Capture files through the artifact tools, and never describe an artifact as \
saved unless the tool result reports capture_status "captured".

Every retrieved item carries provenance. Treat "user_statement" as what the \
user said and "assistant_generated" as a model's conclusion, not a fact.
"""


def build_mcp(settings: Settings) -> FastMCP:
    # The OAuth store resolves its pool lazily, so the server - and every one
    # of its routes - exists before the database does.
    auth = build_auth_provider(settings, client_storage=PostgresKeyValue())
    mcp = FastMCP(
        name="Personal Context",
        instructions=SERVER_INSTRUCTIONS,
        version="0.1.0",
        auth=auth,
        middleware=[
            RateLimitMiddleware(
                per_minute=settings.rate_limit_per_minute,
                burst=settings.rate_limit_burst,
            )
        ],
        # Tool results are data, not secrets to leak in a traceback.
        mask_error_details=settings.is_prod,
    )
    register_all(mcp)
    return mcp


#: Added to every response. The OAuth consent screen is the only HTML this
#: service serves, and it needs no scripts, frames or third-party anything.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (
        b"content-security-policy",
        b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        b"frame-ancestors 'none'; base-uri 'none'",
    ),
)


class SecurityHeadersMiddleware:
    """Pure-ASGI: binds the request id and stamps security headers.

    Written against the raw ASGI interface rather than Starlette's
    BaseHTTPMiddleware because the MCP endpoint streams and BaseHTTPMiddleware
    buffers the response body.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = {k.lower(): v for k, v in scope.get("headers", [])}
        request_id = incoming.get(b"x-request-id", b"").decode("latin-1") or secrets.token_hex(8)
        token = request_id_var.set(request_id)

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                if b"x-request-id" not in present:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


def _verified_key(request: Request, store: LocalObjectStore) -> tuple[str, str] | None:
    """Validate a locally-issued signed URL. Returns (key, bucket) or None."""
    params = request.query_params
    key, bucket = params.get("key", ""), params.get("bucket", "")
    try:
        expires = int(params.get("expires", "0"))
    except ValueError:
        return None
    if not key or not store.verify(key, bucket, expires, params.get("sig", "")):
        return None
    return key, bucket


def _build_routes(settings: Settings, mcp_app: Any) -> list[Route | Mount]:
    """Build every route as a closure over THIS app's settings.

    Handlers deliberately do not reach for the process-wide `get_settings()`:
    an app constructed with explicit settings must behave according to those
    settings, which also makes the security boundary testable without
    manipulating the environment.
    """

    async def root(request: Request) -> Response:
        # Identity only. Nothing here depends on, or reveals, user content.
        return JSONResponse(
            {
                "service": "personal-context-mcp",
                "mcp_endpoint": settings.resource_url,
                "authorization": "required" if settings.auth_mode != "none" else "disabled",
                "documentation": "https://github.com/",
            }
        )

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    async def readyz(request: Request) -> Response:
        try:
            await get_services().db.fetchval("SELECT 1")
        except Exception:
            logger.exception("readiness check failed")
            return JSONResponse({"ready": False}, status_code=503)
        return JSONResponse({"ready": True})

    def local_store() -> LocalObjectStore | None:
        # Deterministic from configuration, so a signature can be checked
        # before any running state is touched.
        if settings.storage_backend != "local":
            return None
        store = build_object_store(settings)
        return store if isinstance(store, LocalObjectStore) else None

    async def local_download(request: Request) -> Response:
        store = local_store()
        if store is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        verified = _verified_key(request, store)
        if verified is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        key, bucket = verified
        try:
            data = await store.get(key, bucket=bucket)
        except Exception:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "content-disposition": "attachment",
                "x-content-type-options": "nosniff",
            },
        )

    async def local_upload(request: Request) -> Response:
        store = local_store()
        if store is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        verified = _verified_key(request, store)
        if verified is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        key, bucket = verified
        body = await request.body()
        if len(body) > settings.max_artifact_file_bytes:
            return JSONResponse({"error": "too large"}, status_code=413)
        await store.put(
            key,
            body,
            mime_type=request.headers.get("content-type", "application/octet-stream"),
            bucket=bucket,
        )
        return JSONResponse({"ok": True})

    def worker_route(handler):  # type: ignore[no-untyped-def]
        # Authorization is decided from settings alone, so an unauthenticated
        # caller gets 401 whether or not the database happens to be up. A 500
        # would confirm the endpoint exists and is wired to something.
        async def endpoint(request: Request) -> Response:
            if not worker_api.authorize(request, settings):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                services = get_services()
            except RuntimeError:
                return JSONResponse({"error": "unavailable"}, status_code=503)
            return await handler(request, services)

        return endpoint

    return [
        Route("/", root, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Route("/worker/jobs/lease", worker_route(worker_api.lease_jobs), methods=["POST"]),
        Route("/worker/jobs/ack", worker_route(worker_api.ack_job), methods=["POST"]),
        Route("/worker/edits", worker_route(worker_api.push_edits), methods=["POST"]),
        Route("/artifacts/download", local_download, methods=["GET"]),
        Route("/artifacts/upload", local_upload, methods=["PUT", "POST"]),
        # The MCP app owns its own path plus the /.well-known OAuth metadata.
        Mount("/", app=mcp_app),
    ]


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    problems = settings.validate_startup()
    if problems:
        for problem in problems:
            logger.error("configuration error", extra={"problem": problem})
        raise SystemExit(
            "Refusing to start. Fix the configuration problems above (see .env.example)."
        )

    # Everything is registered up front: the MCP endpoint, the worker API and
    # the health routes all exist from import time. The lifespan only opens
    # and closes state. This matters for more than tidiness - it is what makes
    # an unauthenticated request get a 401 rather than a 404 when the database
    # is unreachable, so a database outage cannot look like "no such endpoint".
    mcp = build_mcp(settings)
    mcp_app = mcp.http_app(
        path=settings.mcp_path,
        stateless_http=True,  # Cloud Run scales to zero; no sticky sessions.
        allowed_hosts=settings.allowed_hosts or None,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        db = await Database.connect(settings)
        set_database(db)
        services = await build_services(settings, db)
        set_services(services)

        if settings.storage_backend == "supabase":
            with contextlib.suppress(Exception):
                await services.store.ensure_buckets()  # type: ignore[attr-defined]

        logger.info(
            "personal-context-mcp ready",
            extra={
                "mcp_endpoint": settings.resource_url,
                "auth_mode": settings.auth_mode,
                "classifier": settings.classifier,
                "storage": settings.storage_backend,
                "embeddings": settings.embedding_provider,
            },
        )
        async with mcp_app.router.lifespan_context(app):
            try:
                yield
            finally:
                await services.aclose()
                set_services(None)
                set_database(None)

    routes = _build_routes(settings, mcp_app)

    middleware = [
        Middleware(SecurityHeadersMiddleware),
        Middleware(
            CORSMiddleware,
            # MCP clients are server-side; no browser origin needs to read
            # these responses. The OAuth endpoints are navigated to, not
            # fetched cross-origin.
            allow_origins=[],
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["authorization", "content-type", "mcp-protocol-version"],
            expose_headers=["mcp-session-id", "www-authenticate"],
            max_age=600,
        ),
    ]

    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "personal_context_mcp.server:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
