"""Plain-SQL migration runner.

Numbered files in `migrations/` applied once each, in order, inside a
transaction, recorded in `schema_migrations`. No Alembic: the schema is owned
by hand-written SQL and this is all the machinery that needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import asyncpg

from personal_context.logging_setup import get_logger
from personal_context.util import sha256_text

logger = get_logger(__name__)

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str

    @property
    def sha256(self) -> str:
        return sha256_text(self.sql)


def migrations_dir() -> Path:
    """Locate `migrations/` whether running from the repo or a container."""
    for candidate in (
        Path(__file__).resolve().parents[3] / "migrations",  # repo checkout
        Path("/app/migrations"),  # container image
        Path.cwd() / "migrations",
    ):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not locate the migrations/ directory")


def load_migrations(directory: Path | None = None) -> list[Migration]:
    directory = directory or migrations_dir()
    migrations = [
        Migration(version=path.stem, path=path, sql=path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.sql"))
    ]
    if not migrations:
        raise FileNotFoundError(f"No .sql migrations found in {directory}")
    return migrations


async def apply_migrations(conn: asyncpg.Connection, directory: Path | None = None) -> list[str]:
    """Apply every unapplied migration. Returns the versions applied."""
    await conn.execute(_BOOTSTRAP)
    applied = {
        row["version"]: row["sha256"]
        for row in await conn.fetch("SELECT version, sha256 FROM schema_migrations")
    }

    newly_applied: list[str] = []
    for migration in load_migrations(directory):
        if migration.version in applied:
            if applied[migration.version] != migration.sha256:
                # A migration that has already run was edited afterwards. The
                # database and the file no longer agree and silently ignoring
                # that is how schemas drift.
                raise RuntimeError(
                    f"Migration {migration.version} was modified after it was applied. "
                    "Add a new migration instead of editing an applied one."
                )
            continue

        logger.info("applying migration", extra={"version": migration.version})
        async with conn.transaction():
            await conn.execute(migration.sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, sha256) VALUES ($1, $2)",
                migration.version,
                migration.sha256,
            )
        newly_applied.append(migration.version)

    return newly_applied


async def migration_status(conn: asyncpg.Connection, directory: Path | None = None) -> dict:
    await conn.execute(_BOOTSTRAP)
    applied = {row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")}
    all_versions = [m.version for m in load_migrations(directory)]
    return {
        "applied": [v for v in all_versions if v in applied],
        "pending": [v for v in all_versions if v not in applied],
    }
