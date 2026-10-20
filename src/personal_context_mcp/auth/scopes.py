"""Capability scopes.

The vocabulary is intentionally small. Every MCP tool declares the scopes it
needs; FastMCP turns an unmet requirement into a spec-correct
``403 insufficient_scope`` challenge naming exactly what is missing.

HOW SCOPES ARE GRANTED
----------------------
Two paths, chosen by ``PC_AUTH_MODE``:

* ``jwt`` - an external identity provider mints these scopes into the access
  token's ``scope`` claim. The token is authoritative.
* ``google`` / ``github`` - the upstream provider cannot mint scopes it has
  never heard of, so the token proves *identity only*. Capability scopes are
  then granted server-side from ``PC_CLIENT_SCOPE_POLICY``, keyed on the
  registered OAuth client's id or on the app its redirect URIs belong to. This is a real, enforced boundary - it is just
  administered by the operator rather than requested by the client.

Both paths end with the scopes on ``AccessToken.scopes``, so tool code and the
challenge machinery do not care which was used.
"""

from __future__ import annotations

CONTEXT_READ = "context:read"
MEMORY_READ = "memory:read"
MEMORY_WRITE = "memory:write"
SESSIONS_READ = "sessions:read"
SESSIONS_WRITE = "sessions:write"
PROJECTS_READ = "projects:read"
PROJECTS_WRITE = "projects:write"
LISTS_READ = "lists:read"
LISTS_WRITE = "lists:write"
NOTES_READ = "notes:read"
NOTES_WRITE = "notes:write"
ARTIFACTS_READ = "artifacts:read"
ARTIFACTS_WRITE = "artifacts:write"
REVIEW = "review:write"
EMAIL_SEND = "email:send"

ALL_SCOPES: tuple[str, ...] = (
    CONTEXT_READ,
    MEMORY_READ,
    MEMORY_WRITE,
    SESSIONS_READ,
    SESSIONS_WRITE,
    PROJECTS_READ,
    PROJECTS_WRITE,
    LISTS_READ,
    LISTS_WRITE,
    NOTES_READ,
    NOTES_WRITE,
    ARTIFACTS_READ,
    ARTIFACTS_WRITE,
    REVIEW,
    EMAIL_SEND,
)

READ_SCOPES: tuple[str, ...] = tuple(s for s in ALL_SCOPES if s.endswith(":read"))

#: Granted when a client matches no policy entry and no default is configured.
#: Read-only by default: a newly connected client can retrieve context but
#: cannot mutate anything until the operator says so.
DEFAULT_SCOPES: tuple[str, ...] = READ_SCOPES


def expand(scopes: list[str] | tuple[str, ...]) -> set[str]:
    """Expand policy shorthands.

    ``"*"`` means every scope; ``"read"`` means every ``:read`` scope; anything
    else must be a literal scope name. Unknown names are dropped rather than
    silently widening access.
    """
    out: set[str] = set()
    for raw in scopes:
        scope = raw.strip()
        if scope == "*":
            out.update(ALL_SCOPES)
        elif scope == "read":
            out.update(READ_SCOPES)
        elif scope in ALL_SCOPES:
            out.add(scope)
    return out


def resolve_client_scopes(
    policy: dict[str, list[str]],
    *,
    client_id: str | None,
    app: str | None,
) -> set[str]:
    """Pick the scope set for a registered OAuth client.

    Matching, most specific first:
      1. exact ``client_id`` (issued by this server at registration)
      2. the client's ``app`` ("chatgpt", "claude", "gemini" or "grok"),
         recognised from its redirect URIs by auth/clients.py. The display
         name the client chose for itself is never used: anyone can register
         a client called "ChatGPT".
      3. the ``"*"`` default
      4. :data:`DEFAULT_SCOPES`
    """
    if client_id and client_id in policy:
        return expand(policy[client_id])

    if app and app in policy:
        return expand(policy[app])

    if "*" in policy:
        return expand(policy["*"])

    return set(DEFAULT_SCOPES)


def provider_from_client(client_name: str | None, client_id: str | None = None) -> str:
    """Best-effort mapping of an OAuth client to a provider id, for audit rows.

    Guesses from names the client chose itself, so it must never grant
    anything; scopes come from :func:`resolve_client_scopes`.

    Returns ``"unknown"`` rather than guessing when nothing matches - an audit
    trail that invents an attribution is worse than one that admits ignorance.
    """
    haystack = " ".join(filter(None, (client_name, client_id))).lower()
    for provider in ("chatgpt", "claude", "gemini", "grok", "codex"):
        if provider in haystack:
            return provider
    if "openai" in haystack:
        return "chatgpt"
    if "anthropic" in haystack:
        return "claude"
    if "google" in haystack:
        return "gemini"
    if "xai" in haystack or "x.ai" in haystack:
        return "grok"
    return "unknown"
