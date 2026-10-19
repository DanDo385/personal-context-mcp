"""personal-context-mcp command line.

personal-context-mcp serve
personal-context-mcp migrate [--status]
personal-context-mcp check
personal-context-mcp probe <url> [--token TOKEN]
personal-context-mcp import <provider|auto> <archive.zip>
personal-context-mcp reclassify [--limit N]
personal-context-mcp review
personal-context-mcp vault-fill
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import click

from personal_context_mcp.config import Settings, get_settings
from personal_context_mcp.db.migrate import apply_migrations, migration_status
from personal_context_mcp.db.pool import Database, set_database
from personal_context_mcp.digest import build_digest
from personal_context_mcp.extraction.deep import (
    DeepExtractor,
    estimate_cost,
    extraction_candidates,
    vault_targets,
)
from personal_context_mcp.importers.adapters import IMPORTERS
from personal_context_mcp.importers.reclassify import (
    reclassify_conversations,
    stale_conversations,
    summarise,
)
from personal_context_mcp.importers.runner import ImportRunner
from personal_context_mcp.logging_setup import configure_logging
from personal_context_mcp.probe import DEFAULT_PROTOCOL_VERSION, run_probe
from personal_context_mcp.services import build_services, set_services
from personal_context_mcp.setup_wizard import Answers, build_env, next_steps
from personal_context_mcp.vault_fill import fill_vault
from personal_context_mcp.vault_plan import VaultCandidate, vault_candidates
from personal_context_mcp.wiki.service import WikiService


async def _with_services(settings: Settings):
    db = await Database.connect(settings)
    set_database(db)
    services = await build_services(settings, db)
    set_services(services)
    return services


def _configure_cli_logging(settings: Settings) -> None:
    """One-off commands print their own readable output, so their log lines
    are limited to warnings unless PC_LOG_LEVEL was set explicitly. The
    server (``serve``) keeps its configured level."""
    explicit = "log_level" in settings.model_fields_set
    configure_logging(settings.log_level if explicit else "WARNING")


@click.group()
@click.version_option(package_name="personal-context-mcp")
def main() -> None:
    """Shared AI context: memory, projects, lists, notes and artifacts."""


@main.command()
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".env.local"),
    show_default=True,
    help="Where to write the settings file.",
)
@click.option("--force", is_flag=True, help="Overwrite the file if it already exists.")
def init(output: Path, force: bool) -> None:
    """Answer a few questions and get a working settings file.

    Generates every secret for you. Safe to run again: it will not overwrite
    an existing file unless you pass --force.
    """
    if output.exists() and not force:
        raise click.ClickException(
            f"{output} already exists. Run again with --force to replace it "
            "(the old secrets will stop working)."
        )

    click.secho("\nLet's set up personal-context-mcp.\n", bold=True)
    click.echo("Press Enter to accept the suggestion shown in [brackets].\n")

    click.echo(
        "1. Where will it run?\n"
        "   local  - only on this computer, to try it out. The AI apps on your phone\n"
        "            and on the web cannot reach it.\n"
        "   online - on the internet (for example Vercel), so ChatGPT, Claude, Gemini\n"
        "            and Grok can all use it. Needs a free Google sign-in app and a\n"
        "            free Supabase database first (the guides walk you through both)."
    )
    mode = click.prompt("   Choose", type=click.Choice(["local", "online"]), default="local")

    click.echo(
        "\n2. Which email do you sign in to your AI apps with? Only this account will\n"
        "   be allowed in. Separate several with commas."
    )
    emails = [e.strip() for e in click.prompt("   Email").split(",") if e.strip() and "@" in e]
    if not emails:
        raise click.ClickException("At least one email address is needed.")

    answers = Answers(mode=mode, emails=emails)  # type: ignore[arg-type]

    if mode == "online":
        click.echo(
            "\n3. What web address will it live at? It must start with https://\n"
            "   (for example https://context.example.com or https://my-memory.vercel.app)."
        )
        answers.base_url = click.prompt("   Address").rstrip("/")
        if not answers.base_url.startswith("https://"):
            raise click.ClickException("The address must start with https://")
        answers.auth = "google"
        click.echo(
            "\n4. Google sign-in app (docs/guides/03-google-sign-in.md shows how to make\n"
            "   one in about 10 minutes). Leave blank to fill in later."
        )
        answers.oauth_client_id = click.prompt("   Client ID", default="", show_default=False)
        answers.oauth_client_secret = click.prompt(
            "   Client secret", default="", show_default=False, hide_input=True
        )
        click.echo(
            "\n5. Supabase database (docs/guides/04-database.md). Paste the connection\n"
            "   string that starts with postgresql:// and the project URL and service key."
        )
        answers.database_url = click.prompt("   Connection string")
        answers.supabase_url = click.prompt(
            "   Project URL (https://....supabase.co)", default="", show_default=False
        )
        if answers.supabase_url:
            answers.supabase_service_key = click.prompt("   Service role key", hide_input=True)
    else:
        click.echo("\n3. Database. The suggestion matches `docker compose up -d db` in this repo.")
        answers.database_url = click.prompt("   Connection string", default=answers.database_url)

    click.echo(
        "\nOptional extras - press Enter to skip; you can add them to the file later.\n"
        "   TypeSafe (Jev) key: sorts years of old chats cheaply. https://console.typesafe.ai/keys\n"
        "   Anthropic key: reads your most important old chats closely and writes proper notes."
    )
    answers.typesafe_api_key = click.prompt(
        "   TypeSafe key", default="", show_default=False, hide_input=True
    )
    answers.anthropic_api_key = click.prompt(
        "   Anthropic key", default="", show_default=False, hide_input=True
    )
    answers.vault_path = click.prompt(
        "\nWhere is (or should be) your Obsidian vault?", default=answers.vault_path
    )

    output.write_text(build_env(answers), encoding="utf-8")
    output.chmod(0o600)
    click.secho(f"\nWrote {output} (readable only by you).", fg="green", bold=True)
    if "static_token" in answers.generated:
        click.echo(
            "\nYour test access token (also saved in the file):\n"
            f"   {answers.generated['static_token']}"
        )
    click.secho("\nNext steps:", bold=True)
    for number, step in enumerate(next_steps(answers), start=1):
        click.echo(f"  {number}. {step}")


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
    _configure_cli_logging(settings)

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
    _configure_cli_logging(settings)

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


@main.command()
@click.argument("url")
@click.option(
    "--token",
    envvar="PC_PROBE_TOKEN",
    default=None,
    help="Bearer token for the full handshake. Without one, only the sign-in "
    "discovery is checked. Also read from PC_PROBE_TOKEN.",
)
@click.option(
    "--protocol-version",
    default=DEFAULT_PROTOCOL_VERSION,
    show_default=True,
    help="MCP protocol revision to ask for in initialize.",
)
@click.option(
    "--no-oauth",
    is_flag=True,
    help="The server uses PC_AUTH_MODE=static or jwt: skip the sign-in discovery checks.",
)
def probe(url: str, token: str | None, protocol_version: str, no_oauth: bool) -> None:
    """Connect to a running server the way an AI app does, and report problems.

    URL is the MCP endpoint, for example https://<your-host>/mcp. Prints every
    response as received; read-only tool calls only.
    """
    report = run_probe(
        url,
        token=token,
        protocol_version=protocol_version,
        oauth=not no_oauth,
        echo=click.echo,
    )
    click.echo()
    for failure in report.failures:
        click.secho(f"  FAIL {failure}", fg="red")
    if report.failures:
        click.secho(f"{len(report.failures)} of {report.checks} checks failed.", fg="red")
        raise SystemExit(1)
    click.secho(f"All {report.checks} checks passed.", fg="green", bold=True)


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
    _configure_cli_logging(settings)

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
    _configure_cli_logging(settings)

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


@main.command(name="propose-session-projects")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="CSV file to write the proposals to.",
)
@click.option("--limit", type=int, default=None, help="Only the newest N untagged sessions.")
def propose_session_projects_cmd(out_path: Path, limit: int | None) -> None:
    """Propose a project for each session saved without one. Dry run: writes nothing.

    Reads in a read-only transaction and writes a CSV with the proposed project,
    a score and the reason for each session, for a person to review.
    """
    from personal_context_mcp.sessions.backfill import untagged_sessions, write_csv

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        db = await Database.connect(settings)
        try:
            proposals = await untagged_sessions(db, limit=limit)
        finally:
            await db.close()
        click.echo(json.dumps(write_csv(proposals, out_path), indent=2))

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
    _configure_cli_logging(settings)

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
    "--legacy-triage",
    is_flag=True,
    help="Select by the deep_extraction threshold instead of Jev's vault plan.",
)
@click.option(
    "--min-importance",
    type=float,
    default=0.0,
    show_default=True,
    help="With --legacy-triage: pass-1 importance (0-1) below which a conversation is skipped.",
)
@click.option("--redo", is_flag=True, help="Also redo conversations already extracted.")
@click.option("--concurrency", type=int, default=None, help="Default: PC_EXTRACTION_CONCURRENCY.")
@click.option("--dry-run", is_flag=True, help="Show what would run and its estimated cost.")
def extract(
    limit: int,
    legacy_triage: bool,
    min_importance: float,
    redo: bool,
    concurrency: int | None,
    dry_run: bool,
) -> None:
    """Claude reads the conversations Jev put in the vault plan and writes their notes.

    For each: a summary, decisions, facts, open questions and topics (written
    through session_checkpoint), plus the Obsidian note itself in the folder
    Jev chose, its project, tags, and any unfinished actions on the shared
    "From past chats" list. Conversations whose sensitivity is in
    PC_NO_EXTERNAL_SENSITIVITY are never sent. Start with --dry-run.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            if legacy_triage:
                candidates = await extraction_candidates(
                    services, limit=limit, min_importance=min_importance, redo=redo
                )
            else:
                candidates = await vault_targets(services, limit=limit, redo=redo)
            click.echo(json.dumps(estimate_cost(candidates, settings), indent=2))
            if dry_run or not candidates:
                for c in candidates[:20]:
                    click.echo(
                        f"  {c.importance or 0:.2f}  {c.destination:<9} {c.chars:>8} chars  {c.title}"
                    )
                return
            if settings.extraction_provider != "anthropic":
                raise click.ClickException(
                    "Set PC_EXTRACTION_PROVIDER=anthropic and PC_ANTHROPIC_API_KEY to run."
                )
            extractor = DeepExtractor(services)
            try:
                report = await extractor.run(
                    [c.conversation_id for c in candidates],
                    concurrency=concurrency,
                    destinations={c.conversation_id: c.destination for c in candidates},
                )
            finally:
                await extractor.aclose()
            click.echo(json.dumps(report.as_dict(), indent=2))
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


@main.command(name="vault-fill")
def vault_fill_command() -> None:
    """Write session indexes and maps from rows already stored.

    Does not extract conversations, call a model, or ask Jev. Links a note
    that names another note's exact title, then queues a wiki render.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> dict[str, Any]:
        services = await _with_services(settings)
        try:
            return await fill_vault(services)
        finally:
            await services.aclose()
            set_services(None)

    click.echo(json.dumps(asyncio.run(run()), indent=2, default=str))


@main.group()
def wiki() -> None:
    """The wiki graph: note links and Maps of Content (MOCs)."""


def _run_wiki(action):  # type: ignore[no-untyped-def]
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            result = await action(WikiService(services))
            click.echo(json.dumps(result, indent=2, default=str))
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


@wiki.command(name="links")
@click.option("--limit", type=int, default=200, show_default=True, help="Notes to process.")
@click.option("--per-note", type=int, default=12, show_default=True, help="Candidates per note.")
@click.option("--redo", is_flag=True, help="Reconsider notes that already have links.")
def wiki_links(limit: int, per_note: int, redo: bool) -> None:
    """Find candidate links; link title mentions; ask Jev about the rest."""

    async def action(service: WikiService) -> dict[str, Any]:
        report = await service.build_links(limit=limit, per_note=per_note, redo=redo)
        return report.as_dict()

    _run_wiki(action)


@wiki.command(name="add-moc")
@click.argument("title")
@click.option("--description", default=None)
@click.option("--parent", default=None, help="Title or slug of the parent map.")
def wiki_add_moc(title: str, description: str | None, parent: str | None) -> None:
    """Create (or update) a Map of Content by hand."""

    async def action(service: WikiService) -> dict[str, Any]:
        moc_id = await service.add_moc(title, description=description, parent=parent)
        return {"moc_id": str(moc_id), "title": title}

    _run_wiki(action)


@wiki.command(name="seed-mocs")
@click.option("--min-mentions", type=int, default=3, show_default=True)
def wiki_seed_mocs(min_mentions: int) -> None:
    """Propose maps from topics that deep extraction found repeatedly."""

    async def action(service: WikiService) -> dict[str, Any]:
        return {"created": await service.seed_mocs(min_mentions=min_mentions)}

    _run_wiki(action)


@wiki.command(name="mocs")
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--redo", is_flag=True, help="Reassign notes that already have a map.")
def wiki_mocs(limit: int, redo: bool) -> None:
    """Ask Jev which map, and which section of it, each note belongs in."""

    async def action(service: WikiService) -> dict[str, Any]:
        return (await service.assign_mocs(limit=limit, redo=redo)).as_dict()

    _run_wiki(action)


@wiki.command(name="render")
@click.option("--all", "everything", is_flag=True, help="Re-render every note and map.")
def wiki_render(everything: bool) -> None:
    """Queue vault writes for notes and maps whose graph changed."""

    async def action(service: WikiService) -> dict[str, Any]:
        return (await service.render(everything=everything)).as_dict()

    _run_wiki(action)


@wiki.command(name="doctor")
def wiki_doctor() -> None:
    """Orphans, notes without a map, empty maps and pending reviews."""

    async def action(service: WikiService) -> dict[str, Any]:
        return await service.doctor()

    _run_wiki(action)


@main.command()
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--days", type=int, default=None, help="Recent sessions window (default 14).")
def digest(output: Path | None, days: int | None) -> None:
    """Write a readable digest of Personal Context (for assistants that can't connect)."""
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> str:
        services = await _with_services(settings)
        try:
            return await build_digest(
                services.db, settings, recent_days=days or settings.gemini_digest_days
            )
        finally:
            await services.aclose()
            set_services(None)

    text = asyncio.run(run())
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {output} ({len(text)} characters).")
    else:
        click.echo(text)


@main.group(name="gemini-sync")
def gemini_sync_group() -> None:
    """Keep a Google Doc of your Personal Context up to date for a Gemini Gem."""


@gemini_sync_group.command(name="authorize")
@click.option("--op-item", default=None, help="1Password item id to store the token in.")
@click.option("--op-vault", default="Private", show_default=True)
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".env.local"),
    show_default=True,
    help="Where to store the token when not using 1Password.",
)
def gemini_sync_authorize(op_item: str | None, op_vault: str, env_file: Path) -> None:
    """Sign in to Google once so the digest can be written to your Drive."""
    import shutil
    import subprocess

    from personal_context_mcp.gemini_sync import DriveSyncError, authorize

    settings = get_settings()
    try:
        token = authorize(settings)
    except DriveSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    if op_item:
        op = shutil.which("op")
        if op is None:
            raise click.ClickException("The 1Password CLI (op) is not installed.")
        subprocess.run(  # noqa: S603 - argument list, no shell
            [
                op,
                "item",
                "edit",
                op_item,
                "--vault",
                op_vault,
                f"Google Drive.refresh_token[concealed]={token}",
                "--format",
                "json",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        click.secho(
            f"Saved to 1Password: op://{op_vault}/{op_item}/Google Drive/refresh_token", fg="green"
        )
        return
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    lines = [line for line in lines if not line.startswith("PC_GOOGLE_DRIVE_REFRESH_TOKEN=")]
    lines.append(f"PC_GOOGLE_DRIVE_REFRESH_TOKEN={token}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    click.secho(f"Saved to {env_file}.", fg="green")


@gemini_sync_group.command(name="push")
@click.option("--dry-run", is_flag=True, help="Build the digest and show its size; don't upload.")
def gemini_sync_push(dry_run: bool) -> None:
    """Build the digest and write it to the Google Doc (created the first time)."""
    from personal_context_mcp.gemini_sync import DriveSyncError, doc_url, push

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            text = await build_digest(
                services.db, settings, recent_days=settings.gemini_digest_days
            )
        finally:
            await services.aclose()
            set_services(None)
        if dry_run:
            click.echo(
                f"Digest ready: {len(text)} characters, {text.count(chr(10) + '## ')} sections."
            )
            return
        try:
            doc_id, created = await push(settings, text)
        except DriveSyncError as exc:
            raise click.ClickException(str(exc)) from exc
        click.secho(
            f"{'Created' if created else 'Updated'} {settings.gemini_digest_doc_name}: {doc_url(doc_id)}",
            fg="green",
        )

    asyncio.run(run())


@main.command(name="vault-plan")
@click.option("--top", type=int, default=25, show_default=True, help="How many to list.")
def vault_plan_cmd(top: int) -> None:
    """Show which imported conversations Jev's answers put in the vault, and where."""
    import collections

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> list[VaultCandidate]:
        services = await _with_services(settings)
        try:
            return await vault_candidates(services.db, settings)
        finally:
            await services.aclose()
            set_services(None)

    chosen = asyncio.run(run())
    if not chosen:
        click.echo("No conversations qualify yet. Import and classify some first.")
        return

    def ages(c: VaultCandidate) -> str:
        return (
            "last 30 days" if c.age_days <= 30 else "1-6 months" if c.age_days <= 182 else "older"
        )

    click.echo(
        f"{len(chosen)} conversations would become notes "
        f"(min score {settings.vault_min_score}, recency bonus {settings.vault_recency_bonus} "
        f"halving every {settings.vault_recency_halflife_days:g} days)."
    )
    for label, counter in (
        ("folder", collections.Counter(c.destination for c in chosen)),
        ("app", collections.Counter(c.provider for c in chosen)),
        ("age", collections.Counter(ages(c) for c in chosen)),
        ("project", collections.Counter(c.project or "(none)" for c in chosen)),
    ):
        click.echo(f"  by {label}: " + ", ".join(f"{k} {v}" for k, v in counter.most_common()))
    click.echo("")
    for c in chosen[:top]:
        click.echo(
            f"  {c.score:.2f}  {c.destination:<9} {c.provider:<7} {c.age_days:>5.0f}d  {c.title}"
        )


@main.command()
@click.option("--limit", type=int, default=25)
def review(limit: int) -> None:
    """Show the open human-review queue."""
    settings = get_settings()
    _configure_cli_logging(settings)

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
