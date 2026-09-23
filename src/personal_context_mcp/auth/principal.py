"""Who is calling, for audit rows and log lines."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastmcp.server.dependencies import get_access_token

from personal_context_mcp.auth.scopes import provider_from_client


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of an MCP tool."""

    subject: str
    client_id: str | None = None
    client_name: str | None = None
    provider: str = "unknown"
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_anonymous(self) -> bool:
        return self.subject == "anonymous"

    def has(self, scope: str) -> bool:
        return scope in self.scopes


ANONYMOUS = Principal(subject="anonymous", provider="unknown")


def current_principal() -> Principal:
    """Build a Principal from the verified access token, if any.

    Returns :data:`ANONYMOUS` when auth is disabled (development only); tool
    code must never treat that as a licence to skip its scope checks, which
    FastMCP enforces before the tool body runs.
    """
    token = get_access_token()
    if token is None:
        return ANONYMOUS

    claims = token.claims or {}
    upstream = claims.get("upstream_claims") or {}
    subject = (
        claims.get("email")
        or upstream.get("email")
        or token.subject
        or claims.get("sub")
        or "unknown"
    )
    client_name = claims.get("pc_client_name")
    provider = claims.get("pc_provider") or provider_from_client(client_name, token.client_id)

    return Principal(
        subject=str(subject),
        client_id=token.client_id,
        client_name=client_name,
        provider=provider,
        scopes=frozenset(token.scopes or ()),
    )
