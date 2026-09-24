"""personal-context-mcp command line.

personal-context-mcp serve
personal-context-mcp migrate [--status]
personal-context-mcp check
personal-context-mcp import <provider|auto> <archive.zip>
personal-context-mcp reclassify [--limit N]
personal-context-mcp review
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from personal_context_mcp.config import Settings, get_settings
from personal_context_mcp.db.migrate import apply_migrations, migration_status
from personal_context_mcp.db.pool import Database, set_database
from personal_context_mcp.extraction.deep import (
    DeepExtractor,
    estimate_cost,
    extraction_candidates,
)
from personal_context_mcp.importers.adapters import IMPORTERS
from personal_context_mcp.importers.reclassify import (
    reclassify_conversations,
    stale_conversations,
    summarise,
)
from personal_context_mcp.importers.runner import ImportRunner
from personal_context_mcp.logging_setup import configure_logging
from personal_context_mcp.services import build_services, set_services


async def _with_services(settings: Settings):
    db = await Database.connect(settings)
    set_database(db)
    services = await build_services(settings, db)
    set_services(services)
    return services


@click.group()
@click.version_option(package_name="personal-context-mcp")
def main() -> None:
    """Shared AI context: memory, projects, lists, notes and artifacts."""


@main.command()
def serve() -> None:
    """Run the MCP server."""
    from personal_context_mcp.server import main as run_server

    run_server()


@main.command()
@click.option("--status", is_flag=True, help="Show pending migrations without applying.")
def migrate(status: bool) -> None:
    """Apply database migrations."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def run() -> None:
        db = await Database.connect(settings)
        try:
            async with db.acquire() as conn:
                if status:
                    click.echo(json.dumps(await migration_status(conn), indent=2))
                    return
                applied = await apply_migrations(conn)
                if applied:
                    click.echo(f"Applied: {', '.join(applied)}")
                else:
                    click.echo("Already up to date.")
        finally:
            await db.close()

    asyncio.run(run())


@main.command()
def check() -> None:
    """Validate configuration and connectivity without starting the server."""
    settings = get_settings()
    configure_logging(settings.log_level)

    problems = settings.validate_startup()
    for problem in problems:
        click.secho(f"  config: {problem}", fg="red")

    async def run() -> int:
        failures = len(problems)
        try:
            db = await Database.connect(settings)
        except Exception as exc:
            click.secho(f"  database: cannot connect ({type(exc).__name__})", fg="red")
            return failures + 1

        try:
            async with db.acquire() as conn:
                state = await migration_status(conn)
            if state["pending"]:
                click.secho(
                    f"  database: {len(state['pending'])} pending migrations "
                    f"({', '.join(state['pending'])}) - run `personal-context-mcp migrate`",
                    fg="yellow",
                )
            else:
                click.secho("  database: connected, schema up to date", fg="green")

            services = await build_services(settings, db)
            set_services(services)

            if settings.storage_backend == "supabase":
                try:
                    await services.store.ensure_buckets()  # type: ignore[attr-defined]
                    click.secho("  storage: buckets present and private", fg="green")
                except Exception as exc:
                    click.secho(f"  storage: {exc}", fg="red")
                    failures += 1
            else:
                click.secho(
                    f"  storage: local filesystem at {settings.local_storage_root}",
                    fg="yellow",
                )

            if settings.classifier == "jev":
                try:
                    from typesafe_sdk import Noul

                    result = await services.classifier.classify_list_candidate(  # type: ignore[union-attr]
                        {"candidate_text": "buy milk", "sensitivity": "normal"},
                        {"shopping": "Things to buy"},
                    )
                    _ = Noul
                    if result.degraded:
                        click.secho(
                            f"  classifier: Jev unreachable ({result.error}); "
                            "the server will fall back to deterministic rules",
                            fg="yellow",
                        )
                    else:
                        click.secho(f"  classifier: Jev reachable ({result.model})", fg="green")
                except Exception as exc:
                    click.secho(f"  classifier: {type(exc).__name__}: {exc}", fg="yellow")
            else:
                click.secho("  classifier: deterministic rules only", fg="yellow")

            click.secho(
                f"  embeddings: {settings.embedding_provider}"
                + ("" if services.embeddings.enabled else " (text search only)"),
                fg="green" if services.embeddings.enabled else "yellow",
            )
            click.echo(f"\n  MCP endpoint: {settings.resource_url}")
            click.echo(f"  Auth mode:    {settings.auth_mode}")
            await services.aclose()
        finally:
            set_services(None)
        return failures

    failures = asyncio.run(run())
    if failures:
        raise SystemExit(1)
    click.secho("\nAll checks passed.", fg="green", bold=True)


@main.command(name="import")
@click.argument("provider", type=click.Choice([*sorted(IMPORTERS), "auto"]))
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--limit", type=int, default=None, help="Stop after N conversations (for a trial run)."
)
@click.option("--no-store-archive", is_flag=True, help="Do not copy the export to cold storage.")
@click.option(
    "--concurrency", type=int, default=None, help="Conversations at once (PC_IMPORT_CONCURRENCY)."
)
def import_export(
    provider: str,
    archive: Path,
    limit: int | None,
    no_store_archive: bool,
    concurrency: int | None,
) -> None:
    """Import a provider export archive.

    Safe to re-run: conversations are keyed on their provider ids and
    attachments on their SHA-256, so nothing is duplicated.
    """
    settings = get_settings()
    if concurrency is not None:
        settings.import_concurrency = concurrency
    configure_logging(settings.log_level)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            runner = ImportRunner(services)
            report = await runner.run(
                archive,
                provider=None if provider == "auto" else provider,
                limit=limit,
                store_archive=not no_store_archive,
            )
            click.echo(json.dumps(report.as_dict(), indent=2))
            if report.limitations:
                click.secho("\nKnown limitations of this export format:", fg="yellow", bold=True)
                for note in report.limitations:
                    click.secho(f"  - {note}", fg="yellow")
            if report.attachments_referenced:
                click.secho(
                    f"\n{report.attachments_referenced} attachment(s) were named in the "
                    "export but their bytes were not present. They are recorded as "
                    "pending_import, NOT as archived files.",
                    fg="yellow",
                )
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


@main.command()
@click.option("--limit", type=int, default=50, help="Maximum objects to reclassify.")
def reclassify(limit: int) -> None:
    """Retry classification for objects a classifier outage left pending."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            rows = await services.db.fetch(
                """
                SELECT object_type, object_id, reason FROM reclassification_queue
                WHERE next_attempt_at <= now()
                ORDER BY next_attempt_at LIMIT $1
                """,
                limit,
            )
            if not rows:
                click.echo("Nothing queued for reclassification.")
                return

            done = 0
            for row in rows:
                if row["object_type"] == "checkpoint":
                    # Imported conversations are redone from their stored
                    # messages. A live checkpoint's state was never stored,
                    # so only imported conversations qualify.
                    imported = await services.db.fetchval(
                        "SELECT imported FROM conversations WHERE id = $1", row["object_id"]
                    )
                    if not imported:
                        continue
                    report = await reclassify_conversations(services, [row["object_id"]])
                    if not report.errors:
                        await services.db.execute(
                            "DELETE FROM reclassification_queue WHERE object_id = $1",
                            row["object_id"],
                        )
                        done += 1
                    continue
                if row["object_type"] != "artifact":
                    continue
                record = await services.artifacts.get(row["object_id"])
                if record is None:
                    await services.db.execute(
                        "DELETE FROM reclassification_queue WHERE object_id = $1",
                        row["object_id"],
                    )
                    continue
                result, _, _ = await services.classification.classify_artifact(
                    {
                        "title": record.title,
                        "description": record.description or "",
                        "artifact_type": record.artifact_type,
                        "source_type": record.source_type,
                        "file_paths": [f.relative_path for f in record.files],
                        "sensitivity": "normal",
                    },
                    object_id=record.id,
                )
                if not result.degraded:
                    await services.db.execute(
                        "DELETE FROM reclassification_queue WHERE object_id = $1", record.id
                    )
                    done += 1
            click.echo(f"Reclassified {done} of {len(rows)} queued objects.")
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


@main.command(name="reclassify-conversations")
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--provider", type=click.Choice(sorted(IMPORTERS)), default=None)
@click.option(
    "--all",
    "include_current",
    is_flag=True,
    help="Also redo conversations already classified under the current question set.",
)
@click.option("--no-cache", is_flag=True, help="Ask Jev again even for identical input.")
@click.option("--concurrency", type=int, default=None, help="Default: PC_IMPORT_CONCURRENCY.")
@click.option("--dry-run", is_flag=True, help="Only count what would be reclassified.")
def reclassify_conversations_cmd(
    limit: int,
    provider: str | None,
    include_current: bool,
    no_cache: bool,
    concurrency: int | None,
    dry_run: bool,
) -> None:
    """Re-run pass-1 classification over imported conversations.

    By default only conversations with no usable result under the current
    question set: never classified, classified by an older QUESTIONS_VERSION,
    or classified while Jev was unavailable. Works from stored messages; the
    export archive is not needed again.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            ids = await stale_conversations(
                services, limit=limit, provider=provider, include_current=include_current
            )
            click.echo(f"{len(ids)} conversation(s) to reclassify.")
            if dry_run or not ids:
                return
            report = await reclassify_conversations(
                services, ids, concurrency=concurrency, use_cache=not no_cache
            )
            click.echo(json.dumps(summarise(report), indent=2))
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


@main.command()
@click.option("--limit", type=int, default=25, show_default=True)
@click.option(
    "--min-importance",
    type=float,
    default=0.5,
    show_default=True,
    help="Pass-1 importance (0-1) below which a conversation is skipped.",
)
@click.option("--redo", is_flag=True, help="Also redo conversations already extracted.")
@click.option("--concurrency", type=int, default=None, help="Default: PC_EXTRACTION_CONCURRENCY.")
@click.option("--dry-run", is_flag=True, help="Show what would run and its estimated cost.")
def extract(
    limit: int, min_importance: float, redo: bool, concurrency: int | None, dry_run: bool
) -> None:
    """Pass 2: a frontier model reads conversations Jev marked for deep extraction.

    Results are written through session_checkpoint, so they are idempotent and
    routed like any live session. Conversations whose sensitivity is in
    PC_NO_EXTERNAL_SENSITIVITY are never sent. Start with --dry-run.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            candidates = await extraction_candidates(
                services, limit=limit, min_importance=min_importance, redo=redo
            )
            click.echo(json.dumps(estimate_cost(candidates, settings), indent=2))
            if dry_run or not candidates:
                for c in candidates[:20]:
                    click.echo(f"  {c.importance or 0:.2f}  {c.chars:>8} chars  {c.title}")
                return
            if settings.extraction_provider != "anthropic":
                raise click.ClickException(
                    "Set PC_EXTRACTION_PROVIDER=anthropic and PC_ANTHROPIC_API_KEY to run."
                )
            extractor = DeepExtractor(services)
            try:
                report = await extractor.run(
                    [c.conversation_id for c in candidates], concurrency=concurrency
                )
            finally:
                await extractor.aclose()
            click.echo(json.dumps(report.as_dict(), indent=2))
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


@main.command()
@click.option("--limit", type=int, default=25)
def review(limit: int) -> None:
    """Show the open human-review queue."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            items = await services.review.list_open(limit=limit)
            if not items:
                click.secho("Review queue is empty.", fg="green")
                return
            for item in items:
                confidence = item["confidence"]
                click.echo(
                    f"{item['id']}  {item['object_type']:12} {item['reason']:28} "
                    f"conf={confidence:.2f}"
                    if confidence is not None
                    else f"{item['id']}  {item['object_type']:12} {item['reason']:28} conf=n/a"
                )
                click.echo(f"    {item['question']}")
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
