"""Test fixtures.

Two tiers:

* **Unit** tests exercise pure logic (path validation, key generation, archive
  guards, confidence policy, dedup hashing). They need nothing external.
* **Integration** tests exercise real SQL against a real Postgres with
  pgvector. They are skipped, with a clear reason, unless PC_TEST_DATABASE_URL
  points at a throwaway database.

Nothing here ever touches a production database: the integration fixture
refuses a URL that does not look like a test database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personal_context_mcp.config import Settings

TEST_DB_ENV = "PC_TEST_DATABASE_URL"


def _test_dsn() -> str | None:
    dsn = os.environ.get(TEST_DB_ENV)
    if not dsn:
        return None
    # Refuse to run destructive schema work against anything not obviously a
    # scratch database. Strip the query string FIRST: a unix-socket DSN puts a
    # path in `?host=`, and splitting on "/" before that reads the socket
    # directory as the database name.
    tail = dsn.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    if "test" not in tail:
        pytest.exit(
            f"{TEST_DB_ENV} must point at a database whose name contains 'test'. "
            f"Refusing to run migrations against {tail!r}.",
            returncode=2,
        )
    return dsn


requires_db = pytest.mark.skipif(
    _test_dsn() is None,
    reason=f"set {TEST_DB_ENV} to a throwaway database to run integration tests",
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="dev",
        database_url=_test_dsn() or "postgres://unused",
        auth_mode="static",
        static_tokens={"test-token": "tester@example.com"},
        allowed_subjects=["tester@example.com"],
        classifier="rules",
        storage_backend="local",
        local_storage_root=str(tmp_path / "storage"),
        obsidian_enabled=True,
        typesafe_api_key="unused",
        oauth_signing_key="test-signing-key",
        worker_api_key="test-worker-key",
    )


@pytest.fixture
async def db(settings: Settings):
    """A migrated database, wrapped so each test starts from a clean schema."""
    from personal_context_mcp.db.migrate import apply_migrations
    from personal_context_mcp.db.pool import Database

    dsn = _test_dsn()
    if dsn is None:
        pytest.skip(f"set {TEST_DB_ENV} to run this test")

    database = await Database.connect(settings)
    async with database.acquire() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        await apply_migrations(conn)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def services(settings: Settings, db):
    from personal_context_mcp.services import build_services, set_services

    built = await build_services(settings, db)
    set_services(built)
    try:
        yield built
    finally:
        set_services(None)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for name in ("00-Inbox", "20-Projects", "30-Research", "60-Resources"):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
async def live_server(settings):
    """Run the real app on a loopback port.

    FastMCP's client speaks httpx2 and does not honour an injected ASGI
    transport, so an in-process shim does not work. A real socket also tests
    the streaming transport rather than a stand-in for it.
    """
    import asyncio
    import socket

    import uvicorn

    from personal_context_mcp.server import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    settings.base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("uvicorn did not start")

    try:
        yield settings
    finally:
        server.should_exit = True
        await task
