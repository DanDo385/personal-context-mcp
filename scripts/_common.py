"""Shared entry point for the per-provider import scripts.

These exist because the brief asked for them by name; they are one-line
wrappers over `personal-context-mcp import <provider> <archive>`, which is the
supported interface and the one that gets the argument parsing, the report
and the limitation notes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def run_import(provider: str) -> int:
    parser = argparse.ArgumentParser(description=f"Import a {provider} export archive.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N conversations (trial run).")
    parser.add_argument("--no-store-archive", action="store_true")
    args = parser.parse_args()

    from personal_context_mcp.config import get_settings
    from personal_context_mcp.db.pool import Database, set_database
    from personal_context_mcp.importers.runner import ImportRunner
    from personal_context_mcp.logging_setup import configure_logging
    from personal_context_mcp.services import build_services, set_services

    settings = get_settings()
    configure_logging(settings.log_level)

    async def main() -> int:
        db = await Database.connect(settings)
        set_database(db)
        services = await build_services(settings, db)
        set_services(services)
        try:
            report = await ImportRunner(services).run(
                args.archive,
                provider=provider,
                limit=args.limit,
                store_archive=not args.no_store_archive,
            )
            json.dump(report.as_dict(), sys.stdout, indent=2)
            print()
            return 1 if report.errors else 0
        finally:
            await services.aclose()
            set_services(None)
            set_database(None)

    return asyncio.run(main())
