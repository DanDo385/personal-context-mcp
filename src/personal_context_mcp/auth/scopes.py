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
  registered OAuth client. This is a real, enforced boundary - it is just
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
)

READ_SCOPES: tuple[str, ...] = tuple(s for s in ALL_SCOPES if s.endswith(":read"))

#: Granted when a client matches no policy entry and no default is configured.
#: Read-only by default: a newly connected client can retrieve context but
#: cannot mutate anything until the operator says so.
DEFAULT_SCOPES: tuple[str, ...] = READ_SCOPES

#: Client-name aliases for policy matching. Gemini's MCP client identifies as
#: "Spark", never as "Gemini".
_CLIENT_ALIASES: dict[str, str] = {"spark": "gemini"}


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
    client_name: str | None,
) -> set[str]:
    """Pick the scope set for a registered OAuth client.

    Matching, most specific first:
      1. exact ``client_id``
      2. a key that appears (case-insensitively) in the client's display name,
         which is how "chatgpt" / "claude" / "gemini" / "grok" are targeted -
         each provider sends its own product name at registration
      3. the ``"*"`` default
      4. :data:`DEFAULT_SCOPES`
    """
    if client_id and client_id in policy:
        return expand(policy[client_id])

    if client_name:
        lowered = client_name.lower()
        # Gemini registers as "Spark" (Custom apps for Spark), so a policy
        # keyed on "gemini" would otherwise miss it and silently drop the
        # client to the read-only default, hiding every write tool.
        candidates = {lowered}
        for alias, canonical in _CLIENT_ALIASES.items():
            if alias in lowered:
                candidates.add(canonical)
        for key, scopes in policy.items():
            if key not in ("*", "") and any(key.lower() in candidate for candidate in candidates):
                return expand(scopes)

    if "*" in policy:
        return expand(policy["*"])

    return set(DEFAULT_SCOPES)


def provider_from_client(client_name: str | None, client_id: str | None = None) -> str:
    """Best-effort mapping of an OAuth client to a provider id, for audit rows.

    Returns ``"unknown"`` rather than guessing when nothing matches - an audit
    trail that invents an attribution is worse than one that admits ignorance.
    """
    haystack = " ".join(filter(None, (client_name, client_id))).lower()
    for provider in ("chatgpt", "claude", "gemini", "grok"):
        if provider in haystack:
            return provider
    # Gemini registers its MCP client as "Spark" (Custom apps for Spark), so
    # the product name never appears in the client name. Map it explicitly or
    # Gemini traffic is attributed to "unknown" in the audit trail.
    if "spark" in haystack:
        return "gemini"
    if "openai" in haystack:
        return "chatgpt"
    if "anthropic" in haystack:
        return "claude"
    if "google" in haystack:
        return "gemini"
    if "xai" in haystack or "x.ai" in haystack:
        return "grok"
    return "unknown"
