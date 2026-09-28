"""`personal-context-mcp init` writes a file the server accepts."""

from __future__ import annotations

import stat
from pathlib import Path

from click.testing import CliRunner

from personal_context_mcp.cli import main
from personal_context_mcp.config import Settings
from personal_context_mcp.setup_wizard import Answers, build_env


def _load(path: Path) -> Settings:
    return Settings(_env_file=path)  # type: ignore[call-arg]


def test_local_answers_produce_a_valid_configuration(tmp_path: Path) -> None:
    answers = Answers(mode="local", emails=["me@example.com"])
    env = tmp_path / ".env"
    env.write_text(build_env(answers))

    settings = _load(env)
    assert settings.validate_startup() == []
    assert settings.auth_mode == "static"
    assert settings.static_tokens == {answers.generated["static_token"]: "me@example.com"}
    assert settings.storage_backend == "local"
    assert settings.classifier == "rules"


def test_online_answers_pass_production_validation(tmp_path: Path) -> None:
    answers = Answers(
        mode="online",
        emails=["me@example.com", "work@example.com"],
        base_url="https://memory.example.com",
        auth="google",
        oauth_client_id="123-abc.apps.googleusercontent.com",
        oauth_client_secret="secret",
        database_url="postgresql://u:p@db.example.com:6543/postgres",
        supabase_url="https://abc.supabase.co",
        supabase_service_key="service",
        typesafe_api_key="ts",
        anthropic_api_key="sk-ant",
    )
    env = tmp_path / ".env"
    env.write_text(build_env(answers))

    settings = _load(env)
    assert settings.validate_startup() == []
    assert settings.is_prod
    assert settings.allowed_hosts == ["memory.example.com"]
    assert settings.allowed_subjects == ["me@example.com", "work@example.com"]
    assert settings.classifier == "jev"
    assert settings.extraction_provider == "anthropic"
    # The worker and the server agree on the shared key.
    text = env.read_text()
    key = answers.generated["worker_api_key"]
    assert f"PC_WORKER_API_KEY={key}" in text and f"OBSIDIAN_SYNC_API_KEY={key}" in text


def test_every_run_generates_fresh_secrets() -> None:
    a = build_env(Answers(mode="local", emails=["me@example.com"]))
    b = build_env(Answers(mode="local", emails=["me@example.com"]))
    assert a != b


def test_init_writes_a_private_file_and_refuses_to_overwrite(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / ".env"
    # local, email, database (default), TypeSafe (skip), Anthropic (skip), vault (default)
    answers = "local\nme@example.com\n\n\n\n\n"

    result = runner.invoke(main, ["init", "--output", str(output)], input=answers)
    assert result.exit_code == 0, result.output
    assert "Next steps" in result.output
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert _load(output).validate_startup() == []

    again = runner.invoke(main, ["init", "--output", str(output)], input=answers)
    assert again.exit_code != 0
    assert "already exists" in again.output


def test_env_local_overrides_env(tmp_path: Path, monkeypatch) -> None:
    """Settings load .env, then .env.local on top; a real environment variable beats both."""
    monkeypatch.chdir(tmp_path)
    for name in ("PC_BASE_URL", "PC_LOG_LEVEL", "PC_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "PC_DATABASE_URL=postgres://from-env\nPC_BASE_URL=http://from-env\nPC_LOG_LEVEL=DEBUG\n"
    )
    (tmp_path / ".env.local").write_text("PC_BASE_URL=https://from-env-local\n")

    settings = Settings()
    assert str(settings.base_url).rstrip("/") == "https://from-env-local"
    assert settings.log_level == "DEBUG"  # only in .env, still read

    monkeypatch.setenv("PC_BASE_URL", "https://from-process")
    assert str(Settings().base_url).rstrip("/") == "https://from-process"


def test_init_writes_env_local_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init"], input="local\nme@example.com\n\n\n\n\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".env.local").exists()
    assert not (tmp_path / ".env").exists()
