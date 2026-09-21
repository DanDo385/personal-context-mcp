"""asyncpg connection pool with the small amount of glue we actually need.

Deliberately thin: no ORM, no query builder. Every statement in this codebase
is hand-written SQL in the service module that owns the table, which keeps the
security-relevant queries readable.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from personal_context.config import Settings
from personal_context.logging_setup import get_logger

logger = get_logger(__name__)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Make JSONB round-trip as dict/list, and register pgvector text I/O."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    # pgvector's wire format is not known to asyncpg. Its text representation
    # ("[1,2,3]") is accepted on input and easy to parse on output, which is
    # plenty for our volumes and avoids a binary-codec dependency.
    #
    # This must tolerate the type being absent: on a brand-new database the
    # pool is opened by `personal-context migrate`, before migration 0001 has
    # created the extension. asyncpg signals that as a bare ValueError from
    # type introspection, not only as UndefinedObjectError.
    try:
        await conn.set_type_codec(
            "vector",
            encoder=_encode_vector,
            decoder=_decode_vector,
            format="text",
        )
    except (asyncpg.exceptions.UndefinedObjectError, ValueError):
        logger.info(
            "pgvector type not registered on this connection; "
            "run migrations, then reconnect to enable vector search"
        )


def _encode_vector(value: Sequence[float] | None) -> str | None:
    if value is None:
        return None
    return "[" + ",".join(f"{float(v):.7g}" for v in value) + "]"


def _decode_vector(value: str | None) -> list[float] | None:
    if value is None:
        return None
    return [float(part) for part in value.strip("[]").split(",") if part]


class Database:
    """Owns the pool. One instance per process."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, settings: Settings) -> Database:
        dsn = settings.database_url.get_secret_value()
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            init=_init_connection,
            command_timeout=settings.db_statement_timeout_ms / 1000,
            # Supabase's pooler does not support prepared-statement caching.
            statement_cache_size=0,
        )
        logger.info("database pool ready")
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """A connection inside a transaction.

        Checkpoints use this so a half-applied checkpoint never persists.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            yield conn

    # ---------------------------------------------------------------- reads
    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)


_database: Database | None = None


def set_database(db: Database | None) -> None:
    global _database
    _database = db


def get_database() -> Database:
    if _database is None:  # pragma: no cover - programming error
        raise RuntimeError("Database not initialised; server lifespan did not run.")
    return _database
