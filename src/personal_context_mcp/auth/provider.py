"""Authentication providers.

Two things happen on every authenticated request:

1. **Identity.** The bearer token is verified by the configured upstream
   (Google, GitHub, an external JWKS, or a static dev token) and the resulting
   subject is checked against ``PC_ALLOWED_SUBJECTS``. A valid token belonging
   to somebody else is rejected exactly like a forged one.
2. **Capability.** The token's ``scopes`` are replaced with the capability
   scopes this caller is entitled to, so FastMCP's per-tool ``require_scopes``
   checks and its ``insufficient_scope`` challenges work unmodified.

Why capability scopes are not requested by the client in OAuth-proxy mode: an
upstream such as Google will only ever grant scopes it defines. It cannot mint
``memory:write``. Rather than advertise scopes we could not actually verify,
proxy mode treats the upstream token as proof of identity and takes capability
scopes from operator policy. In ``jwt`` mode, where the IdP does mint our
scopes, the token is authoritative instead.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier, StaticTokenVerifier
from key_value.aio.protocols import AsyncKeyValue

from personal_context_mcp.auth.scopes import (
    ALL_SCOPES,
    expand,
    provider_from_client,
    resolve_client_scopes,
)
from personal_context_mcp.config import Settings
from personal_context_mcp.logging_setup import get_logger

logger = get_logger(__name__)

#: Claim names that may carry the end-user's identity, most specific first.
_IDENTITY_CLAIMS = ("email", "preferred_username", "sub", "login")


def _identities(token: AccessToken) -> set[str]:
    """Every identity string this token could be matched on, lower-cased."""
    found: set[str] = set()
    if token.subject:
        found.add(str(token.subject).lower())
    claims: dict[str, Any] = token.claims or {}
    for source in (claims, claims.get("upstream_claims") or {}):
        if not isinstance(source, dict):
            continue
        for name in _IDENTITY_CLAIMS:
            value = source.get(name)
            if isinstance(value, str) and value:
                found.add(value.lower())
    return found


class _PolicyMixin:
    """Applies the allowlist and capability-scope policy to a verified token.

    Mixed in ahead of a FastMCP auth provider so ``verify_token`` runs the
    upstream verification first and post-processes the result.
    """

    settings: Settings

    def _configure_policy(self, settings: Settings) -> None:
        self.settings = settings
        self._allowed = {s.strip().lower() for s in settings.allowed_subjects if s.strip()}

    async def _client_identity(self, token: AccessToken) -> tuple[str | None, str | None]:
        """(client_id, client_name) of the MCP client that presented the token.

        Overridden in proxy mode, where the client id lives in the FastMCP JWT
        rather than on the upstream token.
        """
        return token.client_id, None

    async def _apply_policy(self, token: AccessToken | None) -> AccessToken | None:
        if token is None:
            return None

        if self._allowed:
            identities = _identities(token)
            if not identities & self._allowed:
                # Do not log the rejected identity at INFO: it is somebody's
                # email address and this line ends up in cloud logging.
                logger.warning(
                    "rejected token for non-allowlisted subject",
                    extra={"identity_count": len(identities)},
                )
                return None
        elif self.settings.is_prod:  # pragma: no cover - blocked at startup
            logger.error("no allowlist configured in production; rejecting")
            return None

        client_id, client_name = await self._client_identity(token)
        granted = resolve_client_scopes(
            self.settings.client_scope_policy,
            client_id=client_id,
            client_name=client_name,
        )

        claims = dict(token.claims or {})
        claims["pc_client_name"] = client_name
        claims["pc_provider"] = provider_from_client(client_name, client_id)
        claims["pc_upstream_scopes"] = list(token.scopes)

        return token.model_copy(
            update={"scopes": sorted(granted), "claims": claims},
            deep=False,
        )


class _ProxyPolicyMixin(_PolicyMixin):
    """Policy mixin for OAuth-proxy providers (Google, GitHub)."""

    async def _client_identity(self, token: AccessToken) -> tuple[str | None, str | None]:
        # In proxy mode `token.client_id` is the upstream *user* id, not the
        # MCP client. The MCP client id is a claim of the FastMCP JWT we
        # issued, which we can re-verify locally and cheaply.
        try:
            payload = self.jwt_issuer.verify_token(token.token)  # type: ignore[attr-defined]
            client_id = payload.get("client_id")
        except Exception:
            return None, None

        if not client_id:
            return None, None

        client_name: str | None = None
        try:
            client = await self.get_client(client_id)  # type: ignore[attr-defined]
            client_name = getattr(client, "client_name", None) if client else None
        except Exception:
            client_name = None
        return client_id, client_name


class PolicyGoogleProvider(_ProxyPolicyMixin, GoogleProvider):
    async def verify_token(self, token: str) -> AccessToken | None:
        return await self._apply_policy(await super().verify_token(token))


class PolicyGitHubProvider(_ProxyPolicyMixin, GitHubProvider):
    async def verify_token(self, token: str) -> AccessToken | None:
        return await self._apply_policy(await super().verify_token(token))


class PolicyJWTVerifier(_PolicyMixin, JWTVerifier):
    """External IdP mode: the token's own scopes are authoritative."""

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None:
            return None
        if self._allowed and not (_identities(verified) & self._allowed):
            logger.warning("rejected JWT for non-allowlisted subject")
            return None
        # Intersect with the known vocabulary so an IdP misconfiguration
        # cannot inject a scope this server does not understand.
        granted = expand(list(verified.scopes)) or set()
        claims = dict(verified.claims or {})
        claims["pc_provider"] = provider_from_client(claims.get("client_name"), verified.client_id)
        return verified.model_copy(update={"scopes": sorted(granted), "claims": claims})


class PolicyStaticVerifier(_PolicyMixin, StaticTokenVerifier):
    """Development and test only. Refused when PC_ENVIRONMENT=prod.

    The subject allowlist applies here exactly as it does in OAuth modes: a
    development shortcut for authentication is not a reason to skip checking
    *who* the token belongs to.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None:
            return None

        claims = dict(verified.claims or {})
        claims.setdefault("pc_provider", "cli")
        policied = await self._apply_policy(verified.model_copy(update={"claims": claims}))
        if policied is None:
            return None

        # A local dev token with no configured policy gets the full surface -
        # that is what the mode is for. An explicit policy still wins.
        if not self.settings.client_scope_policy:
            return policied.model_copy(update={"scopes": sorted(ALL_SCOPES)})
        return policied


def build_auth_provider(
    settings: Settings, *, client_storage: AsyncKeyValue | None = None
) -> AuthProvider | None:
    """Construct the auth provider named by ``PC_AUTH_MODE``.

    Returns ``None`` only for ``auth_mode="none"``, which startup validation
    refuses outside development.
    """
    mode = settings.auth_mode

    if mode == "none":
        logger.warning("AUTH DISABLED - every MCP tool is reachable unauthenticated")
        return None

    provider: AuthProvider

    if mode == "static":
        logger.warning("static bearer-token auth enabled (development mode)")
        # StaticTokenVerifier uses this mapping *as* the claims dict and reads
        # the subject from its "sub" key, so the identity fields have to be at
        # the top level rather than nested under "claims".
        provider = PolicyStaticVerifier(
            tokens={
                token: {
                    "client_id": "local-dev",
                    "scopes": list(ALL_SCOPES),
                    "sub": subject,
                    "email": subject,
                }
                for token, subject in settings.static_tokens.items()
            }
        )
        provider._configure_policy(settings)
        return provider

    if mode == "jwt":
        provider = PolicyJWTVerifier(
            jwks_uri=settings.jwt_jwks_uri,
            issuer=settings.jwt_issuer or None,
            audience=settings.jwt_audience or settings.resource_url,
        )
        provider._configure_policy(settings)
        return provider

    common: dict[str, Any] = {
        "client_id": settings.oauth_client_id,
        "client_secret": settings.oauth_client_secret.get_secret_value(),
        "base_url": str(settings.base_url),
        # RFC 8707: the audience clients must request and we must validate.
        "resource_base_url": settings.resource_url,
        "client_storage": client_storage,
        "require_authorization_consent": True,
    }
    signing_key = settings.oauth_signing_key.get_secret_value()
    if signing_key:
        common["jwt_signing_key"] = signing_key

    if mode == "github":
        provider = PolicyGitHubProvider(**common)
    else:
        provider = PolicyGoogleProvider(
            **common,
            required_scopes=["openid", "email"],
        )
    provider._configure_policy(settings)
    logger.info("oauth provider ready", extra={"auth_mode": mode})
    return provider
