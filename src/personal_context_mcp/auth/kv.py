"""Durable key/value store for FastMCP's OAuth state, backed by Postgres.

FastMCP defaults to an encrypted file store under a platform data directory.
On Cloud Run that filesystem is ephemeral and per-instance, so every redeploy
would silently invalidate client registrations and refresh tokens. Backing it
with the database we already have keeps OAuth state durable and shared across
instances, at the cost of about seventy lines.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat

from personal_context_mcp.db.pool import Database

_DEFAULT_COLLECTION = "default"


class PostgresKeyValue:
    """Implements the ``key_value.aio.protocols.AsyncKeyValue`` protocol.

    The pool is resolved on first use rather than injected, so the OAuth
    provider - which needs a store at construction time - can be built before
    the database connects. That keeps every route registered at import time,
    which in turn means the authorization boundary can be tested without a
    database standing behind it.
    """

    def __init__(self, db: Database | None = None, *, namespace: str = "fastmcp") -> None:
        self._explicit_db = db
        self._namespace = namespace

    @property
    def _db(self) -> Database:
        if self._explicit_db is not None:
            return self._explicit_db
        from personal_context_mcp.db.pool import get_database

        return get_database()

    def _collection(self, collection: str | None) -> str:
        return f"{self._namespace}:{collection or _DEFAULT_COLLECTION}"

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            """
            SELECT value FROM oauth_kv
            WHERE collection = $1 AND key = $2
              AND (expires_at IS NULL OR expires_at > now())
            """,
            self._collection(collection),
            key,
        )
        return dict(row["value"]) if row else None

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO oauth_kv (collection, key, value, expires_at)
            VALUES ($1, $2, $3, CASE WHEN $4::float IS NULL THEN NULL
                                     ELSE now() + make_interval(secs => $4::float) END)
            ON CONFLICT (collection, key) DO UPDATE
                SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at
            """,
            self._collection(collection),
            key,
            dict(value),
            float(ttl) if ttl is not None else None,
        )

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        result = await self._db.execute(
            "DELETE FROM oauth_kv WHERE collection = $1 AND key = $2",
            self._collection(collection),
            key,
        )
        return result.rsplit(" ", 1)[-1] != "0"

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        row = await self._db.fetchrow(
            """
            SELECT value, EXTRACT(EPOCH FROM (expires_at - now())) AS remaining
            FROM oauth_kv
            WHERE collection = $1 AND key = $2
              AND (expires_at IS NULL OR expires_at > now())
            """,
            self._collection(collection),
            key,
        )
        if row is None:
            return None, None
        remaining = row["remaining"]
        return dict(row["value"]), float(remaining) if remaining is not None else None

    # -- bulk variants: the protocol requires them; the simple loop is fine at
    # -- our volumes (a handful of registered clients, not a cache tier).
    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(k, collection=collection) for k in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        deleted = 0
        for key in keys:
            deleted += int(await self.delete(key, collection=collection))
        return deleted

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(k, collection=collection) for k in keys]

    async def purge_expired(self) -> int:
        result = await self._db.execute(
            "DELETE FROM oauth_kv WHERE expires_at IS NOT NULL AND expires_at <= now()"
        )
        return int(result.rsplit(" ", 1)[-1])
