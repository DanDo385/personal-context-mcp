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
