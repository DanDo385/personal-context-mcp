"""Token-bucket rate limiting for MCP tool calls.

Per-principal, in-process. Single-user service on a small number of instances,
so an in-memory bucket is honest and sufficient; the alternative (Redis) would
be a dependency bought for nothing. If this ever runs at real concurrency,
swap ``_buckets`` for the ``rate_limit_buckets`` table already in the schema.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from personal_context_mcp.auth.principal import current_principal
from personal_context_mcp.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimitMiddleware(Middleware):
    def __init__(self, *, per_minute: int, burst: int) -> None:
        self._rate = per_minute / 60.0
        self._burst = float(burst)
        self._buckets: dict[str, _Bucket] = {}

    def _allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(tokens=self._burst - 1.0, updated_at=now)
            return True

        elapsed = now - bucket.updated_at
        bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
        bucket.updated_at = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        principal = current_principal()
        key = f"{principal.subject}:{principal.client_id or '-'}"
        if not self._allow(key):
            logger.warning("rate limited", extra={"principal": principal.subject})
            raise ToolError(
                "Rate limit exceeded for this client. Wait a few seconds and retry; "
                "if you were about to make many small calls, batch them instead."
            )
        return await call_next(context)
