"""obsidian-sync command line."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from obsidian_sync.config import WorkerSettings
from obsidian_sync.worker import run_worker


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Local Obsidian sync worker. Outbound connections only."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@main.command()
@click.option("--vault", type=click.Path(path_type=Path), default=None)
@click.option("--api-url", default=None)
@click.option("--dry-run", is_flag=True, help="Report what would be written, write nothing.")
@click.option("--once", is_flag=True, help="Process the current queue and exit.")
def run(vault: Path | None, api_url: str | None, dry_run: bool, once: bool) -> None:
    """Start the worker."""
    settings = WorkerSettings()
    if vault:
        settings.vault_path = vault
    if api_url:
        settings.api_url = api_url
    if dry_run:
        settings.dry_run = True

    if not settings.api_key.get_secret_value():
        raise click.ClickException(
            "OBSIDIAN_SYNC_API_KEY is not set. It must match PC_WORKER_API_KEY on the server."
        )

    if once:
        from obsidian_sync.worker import ObsidianSyncWorker

        async def single() -> None:
            worker = ObsidianSyncWorker(settings)
            processed = await worker._tick()
            click.echo(f"Processed {processed} job(s).")
            await worker._client.aclose()

        asyncio.run(single())
        return

    asyncio.run(run_worker(settings))


@main.command()
def check() -> None:
    """Verify the vault path and server credential."""
    settings = WorkerSettings()
    vault = settings.resolved_vault
    if not vault.is_dir():
        raise click.ClickException(f"Vault not found: {vault}")
    click.secho(f"  vault: {vault}", fg="green")

    missing = [name for name in settings.vault_roots.values() if not (vault / name).is_dir()]
    if missing:
        click.secho(
            f"  note: these roots do not exist yet and will be created on demand: "
            f"{', '.join(sorted(missing))}",
            fg="yellow",
        )

    async def probe() -> None:
        from obsidian_sync.client import SyncClient

        # Lease zero jobs: this proves the URL and the credential work without
        # taking work off the queue or marking anything as failed.
        client = SyncClient(settings)
        original = settings.batch_size
        settings.batch_size = 0
        try:
            await client.lease()
            click.secho("  server: reachable and credential accepted", fg="green")
        finally:
            settings.batch_size = original
            await client.aclose()

    asyncio.run(probe())


if __name__ == "__main__":  # pragma: no cover
    main()
