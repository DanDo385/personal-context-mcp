"""Path security: the tests that matter most if they ever start failing."""

from __future__ import annotations

import pytest

from personal_context_mcp.artifacts.keys import (
    archive_key,
    artifact_prefix,
    object_key,
    sanitize_relative_path,
)
from personal_context_mcp.errors import PathSecurityError
from personal_context_mcp.obsidian.paths import VaultPaths, resolve_in_vault, validate_vault_path

ROOTS = {
    "inbox": "00-Inbox",
    "project": "20-Projects",
    "research": "30-Research",
    "resource": "60-Resources",
}


@pytest.mark.parametrize(
    "path",
    [
        "../../../etc/passwd",
        "20-Projects/../../outside.md",
        "/etc/passwd",
        "//etc/passwd",
        "~/secrets.md",
        "C:/Windows/system.ini",
        "20-Projects/.obsidian/config.json",
        "20-Projects/.git/config",
        ".ssh/authorized_keys",
        "20-Projects/nul.md",
        "20-Projects/note\x00.md",
        "20-Projects/note\n.md",
        "99-Unknown/note.md",
        "20-Projects/a/b/c/d/e/f/g/h/i/deep.md",
        "20-Projects/script.sh",
        "20-Projects/binary.exe",
        "",
        "   ",
    ],
)
def test_vault_path_rejected(path: str) -> None:
    with pytest.raises(PathSecurityError):
        validate_vault_path(path, ROOTS)


@pytest.mark.parametrize(
    "path",
    [
        "00-Inbox/note.md",
        "20-Projects/personal-ai/project.md",
        "20-Projects/personal-ai/Artifacts/dashboard/index.html",
        "30-Research/attention is all you need.md",
        "60-Resources/Lists/reading-list.md",
    ],
)
def test_vault_path_accepted(path: str) -> None:
    assert validate_vault_path(path, ROOTS) == path


def test_vault_path_normalises_separators() -> None:
    assert validate_vault_path("20-Projects\\alpha\\note.md", ROOTS) == "20-Projects/alpha/note.md"


def test_resolve_refuses_symlink_escape(tmp_path) -> None:
    vault = tmp_path / "vault"
    (vault / "20-Projects").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "20-Projects" / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSecurityError):
        resolve_in_vault(vault, "20-Projects/escape/note.md", ROOTS)


def test_resolve_stays_inside_vault(tmp_path) -> None:
    vault = tmp_path / "vault"
    (vault / "20-Projects").mkdir(parents=True)
    resolved = resolve_in_vault(vault, "20-Projects/note.md", ROOTS)
    assert resolved.is_relative_to(vault.resolve())


# ------------------------------------------------------------ object keys


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("src/main.js", "src/main.js"),
        ("../../etc/passwd", "etc/passwd"),
        ("/absolute/file.txt", "absolute/file.txt"),
        ("./a/./b.txt", "a/b.txt"),
        ("weird name!.txt", "weird_name_.txt"),
        ("..\\..\\windows\\file.txt", "windows/file.txt"),
    ],
)
def test_sanitize_relative_path(supplied: str, expected: str) -> None:
    assert sanitize_relative_path(supplied) == expected


@pytest.mark.parametrize("supplied", ["", "..", "../..", "\x00", "/"])
def test_sanitize_rejects_empty_results(supplied: str) -> None:
    with pytest.raises(PathSecurityError):
        sanitize_relative_path(supplied)


def test_sanitize_rejects_excessive_depth() -> None:
    with pytest.raises(PathSecurityError):
        sanitize_relative_path("/".join(["d"] * 20) + "/f.txt")


def test_project_prefix_is_deterministic() -> None:
    prefix = artifact_prefix(
        artifact_slug="Interactive Explainer",
        version=2,
        project_slug="Attention Is All You Need",
    )
    assert prefix == "projects/attention-is-all-you-need/artifacts/interactive-explainer-v2"
    assert (
        object_key(prefix, "src/main.js")
        == "projects/attention-is-all-you-need/artifacts/interactive-explainer-v2/src/main.js"
    )


def test_library_prefix_uses_category_not_model_text() -> None:
    prefix = artifact_prefix(artifact_slug="chart", version=1, semantic_category="research_asset")
    assert prefix.startswith("library/research-assets/")
    assert prefix.endswith("chart-v1")


def test_unknown_category_falls_back_to_inbox() -> None:
    prefix = artifact_prefix(artifact_slug="thing", version=1, semantic_category="nonsense")
    assert "/inbox/" in prefix


def test_archive_key_includes_hash_and_provider() -> None:
    key = archive_key(provider="ChatGPT", filename="my export.zip", sha256="a" * 64)
    assert key.startswith("archives/chatgpt/")
    assert key.endswith("aaaaaaaaaaaa-my_export.zip")


# ------------------------------------------------------------ vault paths


def test_vault_paths_build_from_logical_objects() -> None:
    paths = VaultPaths(ROOTS)
    assert (
        paths.note(note_class="project", title="MCP OAuth", project_slug="personal-ai")
        == "20-Projects/personal-ai/mcp-oauth.md"
    )
    assert paths.note(note_class="research", title="Attention") == "30-Research/attention.md"
    assert (
        paths.artifact_note("dashboard", project_slug="personal-ai")
        == "20-Projects/personal-ai/Artifacts/dashboard/README.md"
    )


def test_vault_artifact_file_path_refuses_traversal_payload() -> None:
    """A traversal attempt is refused outright, not silently flattened.

    Sanitising strips the `..` segments, but what remains ('.ssh/authorized_keys')
    still fails validation because the vault only accepts known content
    extensions. Both layers have to agree before anything is written.
    """
    paths = VaultPaths(ROOTS)
    with pytest.raises(PathSecurityError):
        paths.artifact_file("dash", "../../../.ssh/authorized_keys", project_slug="p")


def test_vault_artifact_file_preserves_legitimate_tree() -> None:
    paths = VaultPaths(ROOTS)
    assert (
        paths.artifact_file("dash", "src/main.js", project_slug="personal-ai")
        == "20-Projects/personal-ai/Artifacts/dash/src/main.js"
    )
