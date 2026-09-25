"""Module-level ASGI application for platforms that import rather than run.

Vercel (and any ASGI server given ``personal_context_mcp.asgi:app``) imports
this module and serves ``app``. Configuration comes from the environment
exactly as it does for ``personal-context-mcp serve``, and the same startup
validation refuses an unsafe configuration.

A platform that imports the app cannot show a startup error to the person
deploying it: a crash at import is just an opaque 500 on every request. So
when the configuration is missing or unsafe, this module serves a stand-in
that answers every request with 503 "not configured" and writes the exact
problems to the platform's logs. The response itself names no setting, so an
unauthenticated caller still learns nothing about the deployment.
"""

from __future__ import annotations

import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

NOT_CONFIGURED = (
    "personal-context-mcp is deployed but not configured yet. The deployment logs "
    "list what is missing; locally, `uv run personal-context-mcp check` shows the same."
)


def not_configured_app(problems: list[str]) -> Starlette:
    for problem in problems:
        print(f"personal-context-mcp configuration error: {problem}", file=sys.stderr)

    async def unavailable(request: Request) -> Response:
        return PlainTextResponse(NOT_CONFIGURED, status_code=503)

    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
    return Starlette(
        routes=[
            Route("/", unavailable, methods=methods),
            Route("/{path:path}", unavailable, methods=methods),
        ]
    )


def build() -> Starlette:
    try:
        from personal_context_mcp.config import get_settings

        problems = get_settings().validate_startup()
    except Exception as exc:  # unparseable settings are a configuration problem too
        problems = [f"settings could not be read: {type(exc).__name__}: {exc}"]
    if problems:
        return not_configured_app(problems)

    from personal_context_mcp.server import create_app

    return create_app()


app = build()
