"""Module-level ASGI application for platforms that import rather than run.

Vercel (and any ASGI server given ``personal_context_mcp.asgi:app``) imports
this module and serves ``app``. Configuration comes from the environment
exactly as it does for ``personal-context-mcp serve``, and the same startup
validation refuses an unsafe configuration before the first request.
"""

from personal_context_mcp.server import create_app

app = create_app()
