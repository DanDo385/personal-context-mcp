"""Worker configuration.

The worker runs on the user's machine and talks OUTBOUND only. It needs no
inbound port, no tunnel, no public hostname and no filesystem exposure.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="OBSIDIAN_"
    )

    #: Base URL of the Personal Context service (outbound target).
    api_url: str = "http://localhost:8080"
    api_key: SecretStr = SecretStr("")
    worker_id: str = "macbook"

    vault_path: Path = Path("~/Obsidian/Vault")
    poll_interval_seconds: float = 20.0
    batch_size: int = 10

    #: Push human edits from the vault back to the canonical store.
    watch_for_edits: bool = True
    #: Roots the worker will write inside. Must match the server's config;
    #: the worker validates independently rather than trusting the job.
    vault_roots: dict[str, str] = {
        "inbox": "00-Inbox",
        "daily": "10-Daily",
        "project": "20-Projects",
        "research": "30-Research",
        "wiki": "40-Wiki",
        "concept": "50-Concepts",
        "resource": "60-Resources",
        "atlas": "70-Atlas",
        "shared": "80-Shared",
        "profile": "85-Profiles",
        "system": "90-System",
        "archive": "99-Archive",
    }

    max_file_bytes: int = 32 * 1024 * 1024
    dry_run: bool = False

    @property
    def resolved_vault(self) -> Path:
        return self.vault_path.expanduser().resolve()
