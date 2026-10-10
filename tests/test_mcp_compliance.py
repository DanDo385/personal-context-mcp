"""MCP compliance: schema completeness, telemetry redaction, client mapping.

Unit tier (no database): these prove the tool manifest a client like Gemini
Custom Apps receives is fully described, that inbound telemetry never records
credentials, and that Gemini's "Spark" client name resolves correctly.
"""

from __future__ import annotations

import pytest

from personal_context_mcp.auth.scopes import provider_from_client, resolve_client_scopes
from personal_context_mcp.telemetry import summarise_rpc, token_fingerprint


@pytest.fixture
async def mcp():
    from personal_context_mcp.config import Settings
    from personal_context_mcp.server import build_mcp

    settings = Settings(
        auth_mode="none",
        database_url="postgres://unused",
        classifier="rules",
        storage_backend="local",
        typesafe_api_key="unused",
        oauth_signing_key="unused",
        worker_api_key="unused",
    )
    return await build_mcp(settings)._list_tools()


def test_every_tool_has_a_description(mcp) -> None:
    undescribed = [t.name for t in mcp if not t.description]
    assert not undescribed, f"tools without descriptions: {undescribed}"


def test_every_property_has_type_and_description(mcp) -> None:
    failures: list[str] = []
    for tool in mcp:
        properties = (tool.parameters or {}).get("properties") or {}
        assert (tool.parameters or {}).get("type") == "object", tool.name
        for name, spec in properties.items():
            if "$ref" in spec:
                continue
            has_type = bool(
                spec.get("type")
                or spec.get("anyOf")
                or spec.get("oneOf")
                or spec.get("enum")
                or spec.get("const")
            )
            if not has_type:
                failures.append(f"{tool.name}.{name}: no explicit type")
            described = bool(spec.get("description")) or any(
                isinstance(o, dict) and o.get("description")
                for o in spec.get("anyOf", []) + spec.get("oneOf", [])
            )
            if not described:
                failures.append(f"{tool.name}.{name}: no description")
    assert not failures, "\n".join(failures)


def test_named_tools_are_registered(mcp) -> None:
    names = {t.name for t in mcp}
    assert {"context_bootstrap", "note_write", "review_list", "list_get"} <= names


def test_telemetry_never_returns_the_raw_token() -> None:
    fingerprinted = token_fingerprint("Bearer secret-token-value")
    assert "secret-token-value" not in fingerprinted
    assert fingerprinted.startswith("Bearer:")
    assert token_fingerprint("") == "-"


def test_telemetry_summarises_tool_calls_without_values() -> None:
    summary = summarise_rpc(
        b'{"jsonrpc": "2.0", "id": 1, "method": "tools/call",'
        b' "params": {"name": "context_bootstrap",'
        b' "arguments": {"user_message": "something private"}}}'
    )
    assert summary["rpc_tool"] == "context_bootstrap"
    assert summary["rpc_param_keys"] == ["user_message"]
    assert "something private" not in str(summary)


def test_telemetry_ignores_non_rpc_bodies() -> None:
    assert summarise_rpc(b"not json") == {}
    assert summarise_rpc(b'{"jsonrpc": "2.0", "id": 1, "result": {}}') == {}


def test_spark_client_resolves_to_gemini() -> None:
    assert provider_from_client("Spark") == "gemini"
    assert provider_from_client("My Spark App") == "gemini"


def test_spark_client_matches_gemini_scope_policy() -> None:
    policy = {"gemini": ["*"]}
    granted = resolve_client_scopes(policy, client_id="unknown", client_name="Spark")
    assert "memory:write" in granted


def test_app_serves_provider_origins_and_telemetry(tmp_path) -> None:
    from personal_context_mcp.config import Settings
    from personal_context_mcp.server import create_app
    from personal_context_mcp.telemetry import McpTelemetryMiddleware

    settings = Settings(
        database_url="postgres://unused",
        auth_mode="static",
        static_tokens={"tok": "tester@example.com"},
        allowed_subjects=["tester@example.com"],
        classifier="rules",
        storage_backend="local",
        local_storage_root=str(tmp_path / "storage"),
        worker_api_key="worker-secret",
        oauth_signing_key="signing",
        typesafe_api_key="unused",
    )
    app = create_app(settings)
    assert "https://gemini.google.com" in settings.cors_allowed_origins
    middlewares = [m.cls for m in app.user_middleware]
    assert McpTelemetryMiddleware in middlewares
