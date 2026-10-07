"""What an unauthenticated caller can learn from the deployed service.

The requirement is absolute: before authentication, zero personal information.
No conversation titles, no filenames, no project names, no memory snippets, no
database ids, no note paths, no artifact metadata, and no user identity.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from personal_context_mcp.config import Settings
from personal_context_mcp.server import create_app

#: Strings that must never appear in an unauthenticated response body.
FORBIDDEN_MARKERS = (
    "conversation",
    "memory",
    "artifact",
    "project",
    "note",
    "@example.com",
    "tester",
)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgres://unused",
        "auth_mode": "static",
        "static_tokens": {"tok": "tester@example.com"},
        "allowed_subjects": ["tester@example.com"],
        "classifier": "rules",
        "storage_backend": "local",
        "worker_api_key": "worker-secret",
        "oauth_signing_key": "signing",
        "typesafe_api_key": "unused",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
async def client(tmp_path):
    settings = _settings(local_storage_root=str(tmp_path / "storage"))
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client, settings


async def test_health_endpoints_are_public_and_empty(client) -> None:
    http_client, _ = client
    response = await http_client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_root_reveals_only_service_identity(client) -> None:
    http_client, _ = client
    response = await http_client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "personal-context-mcp"
    assert body["authorization"] == "required"
    # It names the endpoint - which is not a secret - and nothing else.
    assert set(body) == {"service", "mcp_endpoint", "authorization", "documentation"}


async def test_mcp_without_a_token_is_rejected(client) -> None:
    http_client, settings = client
    response = await http_client.post(
        settings.mcp_path,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert response.status_code in (401, 403)
    body = response.text.lower()
    for marker in FORBIDDEN_MARKERS:
        assert marker not in body, f"unauthenticated response leaked {marker!r}"


async def test_unauthenticated_challenge_points_at_resource_metadata(client) -> None:
    http_client, settings = client
    response = await http_client.post(
        settings.mcp_path,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    challenge = response.headers.get("www-authenticate", "")
    assert "bearer" in challenge.lower()


async def test_invalid_token_is_rejected(client) -> None:
    http_client, settings = client
    response = await http_client.post(
        settings.mcp_path,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "authorization": "Bearer definitely-not-valid",
            "accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code in (401, 403)


async def test_worker_api_requires_the_shared_secret(client) -> None:
    http_client, _ = client
    for path in ("/worker/jobs/lease", "/worker/jobs/ack", "/worker/edits"):
        response = await http_client.post(path, json={})
        assert response.status_code == 401, path
        assert response.json() == {"error": "unauthorized"}


async def test_worker_api_rejects_a_wrong_secret(client) -> None:
    http_client, _ = client
    response = await http_client.post(
        "/worker/jobs/lease", json={}, headers={"x-worker-key": "wrong"}
    )
    assert response.status_code == 401


async def test_security_headers_are_present(client) -> None:
    http_client, _ = client
    response = await http_client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-request-id"]


async def test_csp_does_not_block_the_consent_redirect(client) -> None:
    """Approving consent redirects to the identity provider. Browsers apply
    form-action to that redirect, so a form-action directive strands the user
    on /consent. Regression: 'form-action self' did exactly that."""
    http_client, _ = client
    response = await http_client.get("/")
    assert "form-action" not in response.headers["content-security-policy"]


async def test_signed_local_download_rejects_a_forged_signature(client) -> None:
    http_client, _ = client
    response = await http_client.get(
        "/artifacts/download",
        params={"bucket": "b", "key": "k", "expires": "99999999999", "sig": "forged"},
    )
    assert response.status_code == 403


async def test_signed_local_download_rejects_an_expired_link(client) -> None:
    http_client, _ = client
    response = await http_client.get(
        "/artifacts/download",
        params={"bucket": "b", "key": "k", "expires": "1", "sig": "anything"},
    )
    assert response.status_code == 403


async def test_high_entropy_path_is_not_a_substitute_for_auth(tmp_path) -> None:
    """A secret URL changes nothing about authorization."""
    settings = _settings(server_id="a1b2c3d4e5f6", local_storage_root=str(tmp_path / "s"))
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        assert settings.mcp_path == "/mcp/a1b2c3d4e5f6"
        response = await c.post(
            settings.mcp_path,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
        # Knowing the path gets you exactly as far as not knowing it.
        assert response.status_code in (401, 403)


async def test_wrong_path_does_not_disclose_the_right_one(client) -> None:
    http_client, _ = client
    response = await http_client.get("/mcp/some-guess")
    assert response.status_code in (401, 403, 404, 405)
    assert "a1b2c3" not in response.text


def _google_settings(**overrides: Any) -> Settings:
    return _settings(
        auth_mode="google",
        oauth_client_id="id",
        oauth_client_secret="secret",
        base_url="https://pc.example.com",
        **overrides,
    )


async def test_trailing_slash_is_challenged_not_redirected(client) -> None:
    """A redirect could downgrade to http:// behind a proxy and lose the token."""
    http_client, settings = client
    response = await http_client.post(
        f"{settings.mcp_path}/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert response.status_code in (401, 403)
    assert "location" not in response.headers


async def test_root_protected_resource_metadata_is_an_alias(tmp_path) -> None:
    settings = _google_settings(local_storage_root=str(tmp_path / "s"))
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="https://pc.example.com") as c:
        root = await c.get("/.well-known/oauth-protected-resource")
        scoped = await c.get("/.well-known/oauth-protected-resource/mcp")
    assert root.status_code == scoped.status_code == 200
    assert root.json() == scoped.json()
    assert root.json()["resource"] == "https://pc.example.com/mcp"


async def test_root_metadata_does_not_reveal_a_server_id_path(tmp_path) -> None:
    settings = _google_settings(server_id="a1b2c3d4e5f6", local_storage_root=str(tmp_path / "s"))
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="https://pc.example.com") as c:
        response = await c.get("/.well-known/oauth-protected-resource")
    assert "a1b2c3" not in response.text


async def test_request_log_never_contains_the_credential(client, caplog) -> None:
    http_client, settings = client
    caplog.set_level("INFO", logger="personal_context_mcp.server")
    await http_client.post(
        settings.mcp_path,
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "memory_save", "arguments": {"content": "private words"}},
        },
        headers={
            "authorization": "Bearer very-secret-credential",
            "accept": "application/json, text/event-stream",
            "user-agent": "Gemini-Test/1.0",
        },
    )
    records = [r for r in caplog.records if r.getMessage() == "http request"]
    assert records
    record = records[-1].__dict__
    assert record["status"] == 401
    assert record["user_agent"] == "Gemini-Test/1.0"
    assert record["credential_scheme"] == "bearer"
    logged = repr(record)
    assert "very-secret-credential" not in logged
    assert "private words" not in logged


def test_request_log_summarises_the_rpc_envelope_without_values() -> None:
    from personal_context_mcp.server import _rpc_summary

    body = (
        b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":'
        b'{"name":"memory_save","arguments":{"content":"private words"}}}'
    )
    summary = _rpc_summary(body)
    assert summary == {
        "rpc": [
            {
                "method": "tools/call",
                "id": 7,
                "tool_name": "memory_save",
                "argument_names": ["content"],
            }
        ]
    }
    assert "private words" not in repr(summary)
    assert _rpc_summary(b"{not json") == {"rpc_parse_error": True}


async def _through_basic_middleware(path: str, body: bytes, authorization: str | None) -> bytes:
    """What the OAuth handler behind BasicClientIdMiddleware receives as its body."""
    from personal_context_mcp.server import BasicClientIdMiddleware

    seen = bytearray()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            seen.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None:
            assert int(length) == len(seen)

    headers = [(b"content-type", b"application/x-www-form-urlencoded")]
    headers.append((b"content-length", str(len(body)).encode()))
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    chunks = [body[:5], body[5:]]

    async def receive() -> Any:
        chunk = chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    await BasicClientIdMiddleware(app)(scope, receive, None)
    return bytes(seen)


async def test_basic_auth_client_id_reaches_the_token_handler() -> None:
    """RFC 6749 2.3.1: a client may send client_id only in the Basic header."""
    import base64

    basic = "Basic " + base64.b64encode(b"client%2Done:secret").decode()
    body = await _through_basic_middleware(
        "/token", b"grant_type=refresh_token&refresh_token=r", basic
    )
    assert body == b"grant_type=refresh_token&refresh_token=r&client_id=client-one"


async def test_body_client_id_is_never_replaced() -> None:
    import base64

    basic = "Basic " + base64.b64encode(b"from-header:secret").decode()
    original = b"grant_type=refresh_token&client_id=from-body"
    assert await _through_basic_middleware("/token", original, basic) == original


async def test_other_requests_pass_through_untouched() -> None:
    original = b"grant_type=refresh_token&refresh_token=r"
    assert await _through_basic_middleware("/token", original, None) == original
    assert await _through_basic_middleware("/token", original, "Bearer abc") == original
    assert await _through_basic_middleware("/token", original, "Basic !!notbase64") == original
    assert await _through_basic_middleware("/register", original, "Basic Y2lkOnNlYw==") == original
