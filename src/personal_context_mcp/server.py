"""Server assembly.

Builds the FastMCP server, mounts it in a Starlette app alongside the worker
API and health routes, and owns the lifespan that opens and closes the
database pool and every service.

Route map:

    /                      service identity only; no personal data
    /healthz               liveness
    /readyz                readiness (checks the database)
    /mcp[/<server-id>]     the MCP endpoint, authenticated (a trailing slash
                           is served too, not redirected)
    /.well-known/*         OAuth metadata, added by the auth provider
    /worker/*              local sync worker, shared-secret authenticated
    /artifacts/{up,down}   local storage backend only
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode

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
#:
#: Deliberately no ``form-action``: browsers apply it to every redirect that
#: follows a form submission, and approving the consent form redirects to the
#: identity provider (accounts.google.com). ``form-action 'self'`` made the
#: browser block that redirect, stranding the user on /consent with the
#: single-use approval already spent. The form's target is server-generated,
#: so the directive added nothing.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (
        b"content-security-policy",
        b"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
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


class BasicClientIdMiddleware:
    """Accept a client_id sent only in an HTTP Basic header at /token and /revoke.

    RFC 6749 section 2.3.1 lets a client authenticate with Basic and leave
    client_id out of the form body. The MCP SDK reads client_id from the body
    only, and FastMCP stores every registered client as public, so such a
    client got "invalid_client: Missing client_id" and could never finish
    linking. Copying the id into the body loses nothing: these clients are
    public, PKCE protects the code, and the secret was never checked.
    """

    #: OAuth form posts are a few hundred bytes; a bigger one is passed on as is.
    _MAX_BODY = 64 * 1024

    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def _basic_client_id(scope: Any) -> str | None:
        for name, value in scope.get("headers", []):
            if name.lower() != b"authorization":
                continue
            scheme, _, encoded = value.decode("latin-1").partition(" ")
            if scheme.lower() != "basic":
                return None
            try:
                decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                return None
            client_id, sep, _ = decoded.partition(":")
            return unquote(client_id) if sep and client_id else None
        return None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in ("/token", "/revoke")
        ):
            await self.app(scope, receive, send)
            return
        client_id = self._basic_client_id(scope)
        if client_id is None:
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                break

        fields = parse_qsl(body.decode("latin-1"), keep_blank_values=True)
        if len(body) <= self._MAX_BODY and not any(k == "client_id" for k, _ in fields):
            body = bytearray(urlencode([*fields, ("client_id", client_id)]).encode("latin-1"))
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            scope = {**scope, "headers": headers}

        replayed = False

        async def replay() -> Any:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class PathAliasMiddleware:
    """Serve the paths clients guess at, instead of redirecting or 404ing.

    ``/mcp/`` would otherwise get a 307 to ``/mcp``. A redirect is a bad
    answer to an authenticated POST: behind a TLS-terminating proxy its
    Location can come out as ``http://``, and clients drop the Authorization
    header when a redirect changes origin. Clients that ignore the
    ``resource_metadata`` hint in the 401 challenge look for the protected
    resource metadata at the root ``/.well-known/oauth-protected-resource``;
    RFC 9728 publishes it under the resource's path, so the root is an alias.
    Not with PC_SERVER_ID: the root document would name the secret path to
    anyone who asked.
    """

    def __init__(self, app: Any, *, mcp_path: str, root_metadata: bool) -> None:
        self.app = app
        self.aliases = {f"{mcp_path}/": mcp_path}
        if root_metadata:
            self.aliases["/.well-known/oauth-protected-resource"] = (
                f"/.well-known/oauth-protected-resource{mcp_path}"
            )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] in self.aliases:
            path = self.aliases[scope["path"]]
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)


#: Bodies above this are not parsed for the request log; they are still served.
_LOGGED_BODY_LIMIT = 256 * 1024
_KNOWN_AUTH_SCHEMES = frozenset({"bearer", "basic", "dpop", "negotiate"})


def _credential_scheme(credential: str) -> str | None:
    """The Authorization scheme name, never any part of the credential itself."""
    if not credential:
        return None
    scheme = credential.split(None, 1)[0].lower()
    return scheme if scheme in _KNOWN_AUTH_SCHEMES else "unrecognized"


def _rpc_summary(body: bytes) -> dict[str, Any]:
    """The JSON-RPC envelope of a request body: method, id and tool name.

    Argument names are logged, never their values: those are the user's
    memories and messages.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return {"rpc_parse_error": True}
    messages = payload if isinstance(payload, list) else [payload]
    calls = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        call: dict[str, Any] = {"method": message.get("method"), "id": message.get("id")}
        params = message.get("params")
        if isinstance(params, dict):
            if isinstance(params.get("name"), str):
                call["tool_name"] = params["name"]
            if isinstance(params.get("arguments"), dict):
                call["argument_names"] = sorted(params["arguments"])
            if isinstance(params.get("protocolVersion"), str):
                call["protocol_version"] = params["protocolVersion"]
            client_info = params.get("clientInfo")
            if isinstance(client_info, dict):
                call["client_info"] = {
                    k: client_info.get(k) for k in ("name", "version") if k in client_info
                }
        calls.append(call)
    return {"rpc": calls}


class RequestLogMiddleware:
    """One structured log line per HTTP request, written when it finishes.

    Records path, status, duration, User-Agent, whether a credential was sent
    and its scheme, and for MCP calls the JSON-RPC envelope. The credential
    itself is never logged. The raw body is logged only at DEBUG outside
    production, the same rule as message content everywhere else. The body is
    observed as the app reads it, never buffered here, so a request refused
    before it is read (an unauthenticated one) is logged without an envelope.

    Pure ASGI for the same reason as SecurityHeadersMiddleware: the MCP
    endpoint may stream.
    """

    def __init__(self, app: Any, *, mcp_path: str, log_bodies: bool) -> None:
        self.app = app
        self.mcp_path = mcp_path
        self.log_bodies = log_bodies

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        credential = headers.get(b"authorization", b"").decode("latin-1").strip()
        is_mcp = scope["method"] == "POST" and scope["path"].rstrip("/") == self.mcp_path
        body = bytearray()
        status = 0

        async def receive_wrapper() -> Any:
            message = await receive()
            if message["type"] == "http.request" and len(body) < _LOGGED_BODY_LIMIT:
                body.extend(message.get("body", b""))
            return message

        async def send_wrapper(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive_wrapper if is_mcp else receive, send_wrapper)
        finally:
            fields: dict[str, Any] = {
                "http_method": scope["method"],
                "path": scope["path"],
                "status": status or 500,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "user_agent": headers.get(b"user-agent", b"").decode("latin-1")[:300],
                "credential_scheme": _credential_scheme(credential),
                "mcp_protocol_version": headers.get(b"mcp-protocol-version", b"").decode("latin-1")
                or None,
            }
            if is_mcp and body and len(body) < _LOGGED_BODY_LIMIT:
                fields.update(_rpc_summary(bytes(body)))
                if self.log_bodies:
                    fields["rpc_body"] = body.decode("utf-8", "replace")
            logger.info("http request", extra=fields)


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
        # One JSON body per POST. Stateless mode has no server-to-client
        # stream to keep open, and a JSON reply only needs the client to
        # accept application/json, not also text/event-stream.
        json_response=True,
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
            RequestLogMiddleware,
            mcp_path=settings.mcp_path,
            log_bodies=not settings.is_prod and settings.log_level.upper() == "DEBUG",
        ),
        Middleware(
            PathAliasMiddleware,
            mcp_path=settings.mcp_path,
            root_metadata=not settings.server_id,
        ),
        Middleware(BasicClientIdMiddleware),
        Middleware(
            CORSMiddleware,
            # The provider web apps fetch this endpoint from the browser, so
            # their origins must be listed or discovery dies in preflight.
            # OAuth pages are navigated to, not fetched cross-origin. Auth is
            # a bearer token, so listing origins does not widen a cookie-less
            # caller.
            allow_origins=settings.cors_allowed_origins,
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
