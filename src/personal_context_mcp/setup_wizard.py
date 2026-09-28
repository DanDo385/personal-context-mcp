"""``personal-context-mcp init``: write a working ``.env.local`` by answering questions.

Aimed at someone who has never configured a server. It asks only what it
cannot work out, generates every secret itself, explains each answer in plain
words, and never overwrites an existing file unless asked to.

The file-building half is a pure function (``build_env``) so it is tested
without any prompting.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["local", "online"]


@dataclass
class Answers:
    mode: Mode
    emails: list[str]
    base_url: str = "http://localhost:8080"
    auth: Literal["google", "github", "static"] = "static"
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    database_url: str = "postgresql://postgres:postgres@localhost:5432/personal_context_mcp"
    supabase_url: str = ""
    supabase_service_key: str = ""
    typesafe_api_key: str = ""
    anthropic_api_key: str = ""
    vault_path: str = "~/Obsidian/Personal Context"
    #: Filled in by build_env; returned so the wizard can show the local token.
    generated: dict[str, str] = field(default_factory=dict)


def _token() -> str:
    return secrets.token_hex(32)


def _host(base_url: str) -> str:
    return base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


def build_env(answers: Answers) -> str:
    """Render a complete, commented ``.env.local`` for the given answers."""
    worker_key = _token()
    signing_key = _token()
    answers.generated = {"worker_api_key": worker_key}
    online = answers.mode == "online"
    lines: list[str] = [
        "# personal-context-mcp settings, written by `personal-context-mcp init`.",
        "# This file holds secrets. Never commit it, never paste it anywhere.",
        "# Every setting is explained in .env.example. This file is never committed.",
        "",
        "# ---- Where it runs",
        f"PC_ENVIRONMENT={'prod' if online else 'dev'}",
        f"PC_BASE_URL={answers.base_url}",
    ]
    if online:
        lines.append(f"PC_ALLOWED_HOSTS={_host(answers.base_url)}")

    lines += [
        "",
        "# ---- Who may use it (the email you sign in to your AI apps with)",
        f"PC_ALLOWED_SUBJECTS={','.join(answers.emails)}",
        "",
        "# ---- How apps sign in",
        f"PC_AUTH_MODE={answers.auth}",
    ]
    if answers.auth == "static":
        token = _token()
        answers.generated["static_token"] = token
        lines.append(f'PC_STATIC_TOKENS={{"{token}":"{answers.emails[0]}"}}')
    else:
        lines += [
            f"PC_OAUTH_CLIENT_ID={answers.oauth_client_id}",
            f"PC_OAUTH_CLIENT_SECRET={answers.oauth_client_secret}",
        ]
    lines += [
        f"PC_OAUTH_SIGNING_KEY={signing_key}",
        'PC_CLIENT_SCOPE_POLICY={"*":["*"]}',
        "",
        "# ---- The database",
        f"PC_DATABASE_URL={answers.database_url}",
    ]
    if online:
        lines += ["PC_DB_POOL_MIN_SIZE=0", "PC_DB_POOL_MAX_SIZE=3"]

    lines += ["", "# ---- Where files are kept"]
    if answers.supabase_url:
        lines += [
            "PC_STORAGE_BACKEND=supabase",
            f"PC_SUPABASE_URL={answers.supabase_url}",
            f"PC_SUPABASE_SERVICE_KEY={answers.supabase_service_key}",
        ]
    else:
        lines += ["PC_STORAGE_BACKEND=local", "PC_LOCAL_STORAGE_ROOT=.data/storage"]

    lines += ["", "# ---- Sorting your history (optional)"]
    if answers.typesafe_api_key:
        lines += ["PC_CLASSIFIER=jev", f"PC_TYPESAFE_API_KEY={answers.typesafe_api_key}"]
    else:
        lines.append("PC_CLASSIFIER=rules")
    if answers.anthropic_api_key:
        lines += [
            "PC_EXTRACTION_PROVIDER=anthropic",
            f"PC_ANTHROPIC_API_KEY={answers.anthropic_api_key}",
        ]
    else:
        lines.append("PC_EXTRACTION_PROVIDER=none")

    lines += [
        "",
        "# ---- Obsidian (the worker on your computer uses these two)",
        f"PC_WORKER_API_KEY={worker_key}",
        f"OBSIDIAN_SYNC_API_URL={answers.base_url}",
        f"OBSIDIAN_SYNC_API_KEY={worker_key}",
        f"OBSIDIAN_SYNC_VAULT_PATH={answers.vault_path}",
        "",
    ]
    return "\n".join(lines)


def next_steps(answers: Answers) -> list[str]:
    """What to do after the file is written, in order."""
    steps: list[str] = []
    if answers.mode == "local":
        steps += [
            "Start the database:        docker compose up -d db",
            "Create the tables:         uv run personal-context-mcp migrate",
            "Check everything:          uv run personal-context-mcp check",
            "Start the server:          uv run personal-context-mcp serve",
            "Try it with the token printed above - see docs/guides/02-try-it-locally.md",
        ]
    else:
        if answers.auth != "static" and not answers.oauth_client_id:
            steps.append(
                "Create your sign-in app and paste its id and secret into .env.local:"
                "  docs/guides/03-google-sign-in.md"
            )
        steps += [
            "Create the tables:         uv run personal-context-mcp migrate",
            "Check everything:          uv run personal-context-mcp check",
            "Put it online:             docs/guides/05-put-it-online.md",
            "Connect your AI apps:      docs/guides/06-connect-your-ai-apps.md",
        ]
    return steps
