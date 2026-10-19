"""The image entrypoint decides when a command migrates, by running it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"


def _run(tmp_path: Path, *args: str) -> list[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls"
    log.unlink(missing_ok=True)
    fake = bindir / "personal-context-mcp"
    fake.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    completed = subprocess.run(
        [str(ENTRYPOINT), *args],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    return log.read_text(encoding="utf-8").splitlines()


def test_serve_migrates_and_then_starts(tmp_path: Path) -> None:
    assert _run(tmp_path, "personal-context-mcp", "serve") == [
        "migrate",
        "serve",
    ]


def test_probe_and_migrate_do_not_migrate_first(tmp_path: Path) -> None:
    assert _run(tmp_path, "personal-context-mcp", "probe", "https://example.test/mcp") == [
        "probe https://example.test/mcp",
    ]
    assert _run(tmp_path, "personal-context-mcp", "migrate") == ["migrate"]
    assert _run(tmp_path, "personal-context-mcp", "check") == ["check"]
