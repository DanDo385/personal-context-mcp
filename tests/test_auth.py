"""Authentication, scope policy, and what an unauthenticated caller can see."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.utilities.authorization import AuthContext, require_scopes

from personal_context_mcp.auth import scopes
from personal_context_mcp.auth.provider import _identities, build_auth_provider
from personal_context_mcp.auth.scopes import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    expand,
    provider_from_client,
    resolve_client_scopes,
)
from personal_context_mcp.config import Settings


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgres://unused",
        "auth_mode": "static",
        "static_tokens": {"tok": "me@example.com"},
        "allowed_subjects": ["me@example.com"],
        "classifier": "rules",
        "storage_backend": "local",
    }
    base.update(overrides)
    return Settings(**base)


# ------------------------------------------------------------ scope policy


def test_expand_shorthands() -> None:
    assert expand(["*"]) == set(ALL_SCOPES)
    assert expand(["read"]) == {s for s in ALL_SCOPES if s.endswith(":read")}
    assert expand([scopes.MEMORY_WRITE]) == {scopes.MEMORY_WRITE}


def test_unknown_scopes_are_dropped_not_granted() -> None:
    assert expand(["memory:write", "root:everything", ""]) == {scopes.MEMORY_WRITE}


def test_default_is_read_only() -> None:
    granted = resolve_client_scopes({}, client_id="new-client", client_name="Something New")
    assert granted == set(DEFAULT_SCOPES)
    assert not any(s.endswith(":write") for s in granted)


def test_exact_client_id_wins_over_name_match() -> None:
    policy = {"abc123": ["memory:read"], "chatgpt": ["*"]}
    granted = resolve_client_scopes(policy, client_id="abc123", client_name="ChatGPT")
    assert granted == {scopes.MEMORY_READ}


def test_client_name_substring_match() -> None:
    policy = {"grok": ["read"], "*": ["*"]}
    granted = resolve_client_scopes(policy, client_id="xyz", client_name="Grok Connector")
    assert granted == set(expand(["read"]))


def test_wildcard_default_applies_when_nothing_matches() -> None:
    policy = {"*": ["memory:read", "context:read"]}
    granted = resolve_client_scopes(policy, client_id="q", client_name="Unknown App")
    assert granted == {scopes.MEMORY_READ, scopes.CONTEXT_READ}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ChatGPT", "chatgpt"),
        ("Claude Desktop", "claude"),
        ("Google Gemini", "gemini"),
        ("Grok", "grok"),
        ("OpenAI Connector", "chatgpt"),
        ("Anthropic", "claude"),
        ("Some Random Client", "unknown"),
        (None, "unknown"),
    ],
)
def test_provider_attribution(name: str | None, expected: str) -> None:
    assert provider_from_client(name) == expected


# ---------------------------------------------------------- scope checks


def _ctx(granted: list[str]) -> AuthContext:
    token = AccessToken(
        token="t", client_id="c", scopes=granted, expires_at=None, subject="me@example.com"
    )
    return AuthContext(token=token, component=object())  # type: ignore[arg-type]


def test_require_scopes_allows_and_denies() -> None:
    check = require_scopes(scopes.MEMORY_WRITE)
    assert check(_ctx([scopes.MEMORY_WRITE, scopes.MEMORY_READ])) is True
    assert check(_ctx([scopes.MEMORY_READ])) is False


def test_missing_scopes_are_named_for_the_challenge() -> None:
    check = require_scopes(scopes.MEMORY_WRITE, scopes.SESSIONS_WRITE)
    missing = check.missing_scopes(_ctx([scopes.MEMORY_WRITE]))
    assert missing == {scopes.SESSIONS_WRITE}


def test_absent_token_is_denied() -> None:
    check = require_scopes(scopes.CONTEXT_READ)
    assert check(AuthContext(token=None, component=object())) is False  # type: ignore[arg-type]


# ------------------------------------------------------------- allowlist


def test_identities_collects_every_claim_shape() -> None:
    token = AccessToken(
        token="t",
        client_id="c",
        scopes=[],
        expires_at=None,
        subject="sub-123",
        claims={"email": "Me@Example.com", "upstream_claims": {"preferred_username": "me"}},
    )
    assert _identities(token) == {"sub-123", "me@example.com", "me"}


async def test_non_allowlisted_subject_is_rejected() -> None:
    provider = build_auth_provider(_settings(static_tokens={"tok": "intruder@elsewhere.com"}))
    assert await provider.verify_token("tok") is None


async def test_allowlisted_subject_is_accepted_with_policy_scopes() -> None:
    provider = build_auth_provider(
        _settings(client_scope_policy={"*": ["memory:read", "context:read"]})
    )
    token = await provider.verify_token("tok")
    assert token is not None
    assert set(token.scopes) == {scopes.MEMORY_READ, scopes.CONTEXT_READ}


async def test_invalid_token_is_rejected() -> None:
    provider = build_auth_provider(_settings())
    assert await provider.verify_token("not-a-real-token") is None


# --------------------------------------------------- startup refusals


def test_prod_refuses_auth_none() -> None:
    problems = _settings(
        environment="prod", auth_mode="none", base_url="https://x.example.com"
    ).validate_startup()
    assert any("AUTH_MODE=none" in p for p in problems)


def test_prod_refuses_static_tokens() -> None:
    problems = _settings(environment="prod", base_url="https://x.example.com").validate_startup()
    assert any("AUTH_MODE=static" in p for p in problems)


def test_prod_refuses_plain_http() -> None:
    problems = _settings(environment="prod", base_url="http://x.example.com").validate_startup()
    assert any("https" in p for p in problems)


def test_oauth_mode_requires_an_allowlist() -> None:
    problems = _settings(
        auth_mode="google",
        oauth_client_id="id",
        oauth_client_secret="secret",
        allowed_subjects=[],
    ).validate_startup()
    assert any("ALLOWED_SUBJECTS" in p for p in problems)


def test_oauth_mode_requires_credentials() -> None:
    problems = _settings(auth_mode="google").validate_startup()
    assert any("OAUTH_CLIENT_ID" in p for p in problems)


def test_jev_classifier_requires_a_key() -> None:
    problems = _settings(classifier="jev", typesafe_api_key="").validate_startup()
    assert any("TYPESAFE_API_KEY" in p for p in problems)


def test_threshold_ordering_is_enforced() -> None:
    problems = _settings(review_confidence=0.9, auto_route_confidence=0.5).validate_startup()
    assert any("REVIEW_CONFIDENCE" in p for p in problems)


def test_valid_dev_config_has_no_problems() -> None:
    assert _settings().validate_startup() == []


# ---------------------------------------------------------- resource URI


def test_resource_url_is_a_valid_rfc8707_identifier() -> None:
    settings = _settings(base_url="https://ctx.example.com", server_id="")
    assert settings.resource_url == "https://ctx.example.com/mcp"
    assert "#" not in settings.resource_url


def test_high_entropy_server_id_is_part_of_the_resource_uri() -> None:
    settings = _settings(base_url="https://ctx.example.com", server_id="a1b2c3")
    assert settings.mcp_path == "/mcp/a1b2c3"
    assert settings.resource_url == "https://ctx.example.com/mcp/a1b2c3"
