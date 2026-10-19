"""Which OAuth clients may sign in, and which app a registered client is.

Anyone can register a client (RFC 7591 dynamic registration), and the client
chooses both its display name and its redirect URIs. The redirect URI is the
part that cannot be faked: the authorization code is only ever delivered
there. So it is the redirect URI, never the display name, that decides:

* whether the client may sign in at all (an allowlist of known apps' callback
  URLs plus loopback for local tools), and
* which app the client is, for ``PC_CLIENT_SCOPE_POLICY`` keys such as
  ``"grok"`` and for audit rows.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastmcp.server.auth.redirect_validation import matches_allowed_pattern

#: Callback URLs each hosted app uses, as FastMCP redirect patterns
#: (``*`` matches within the path; a loopback host without a port matches any port).
APP_REDIRECT_PATTERNS: dict[str, tuple[str, ...]] = {
    "chatgpt": (
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://chatgpt.com/connector/oauth/*",
        "https://platform.openai.com/apps-manage/oauth",
    ),
    "claude": (
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    ),
    "gemini": (
        "https://oauth-redirect.googleusercontent.com/*",
        "https://oauth-redirect-sandbox.googleusercontent.com/*",
        "https://oauth-redirect-test.googleusercontent.com/*",
    ),
    "grok": ("https://grok.com/connectors-oauth-exchange-code/*",),
}

#: Local tools (Claude Code, Codex, the probe) receive the code on this machine.
LOOPBACK_REDIRECT_PATTERNS: tuple[str, ...] = ("http://localhost", "http://127.0.0.1")


def allowed_redirect_patterns(extra: Iterable[str] = ()) -> list[str]:
    """Every redirect pattern a client may use: the known apps, loopback, and
    the operator's own additions from ``PC_ALLOWED_REDIRECT_URIS``."""
    patterns = [p for app in APP_REDIRECT_PATTERNS.values() for p in app]
    patterns.extend(LOOPBACK_REDIRECT_PATTERNS)
    patterns.extend(p.strip() for p in extra if p.strip())
    return patterns


def app_for_redirect_uris(redirect_uris: Iterable[object]) -> str | None:
    """The app every one of a client's redirect URIs belongs to, or None.

    A client with no redirect URIs, a loopback one, or URIs spread over more
    than one app is not attributed to any app.
    """
    found: set[str] = set()
    for uri in redirect_uris:
        matches = {
            app
            for app, patterns in APP_REDIRECT_PATTERNS.items()
            if any(matches_allowed_pattern(str(uri), p) for p in patterns)
        }
        if len(matches) != 1:
            return None
        found |= matches
    return found.pop() if len(found) == 1 else None
