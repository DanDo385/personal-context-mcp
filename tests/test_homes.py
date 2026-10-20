"""Logical homes map to configured vault roots, independent of app/provider."""

import pytest

from personal_context_mcp.config import Settings
from personal_context_mcp.errors import ValidationError
from personal_context_mcp.homes import home_metadata
from personal_context_mcp.obsidian.paths import VaultPaths


@pytest.mark.parametrize(
    ("home", "folder"),
    [
        ("projects/memory", "20-Projects/memory"),
        ("lesson/sql", "30-Research/Lessons/sql"),
        ("setup/neovim", "60-Resources/Setup/neovim"),
        ("other", "00-Inbox"),
    ],
)
def test_home_folder_mapping(home, folder):
    paths = VaultPaths(Settings(_env_file=None).obsidian_vault_roots)
    assert paths.home_note(home, "A note") == f"{folder}/a-note.md"
    assert paths.home_note(home, "A list", is_list=True) == f"{folder}/Lists/a-list.md"


def test_home_and_concepts_are_one_home_with_distinct_tags():
    assert home_metadata(None, "memory", ["SQL", "sql", "concept/Postgres"]) == {
        "home": "projects/memory",
        "tags": ["projects/memory", "concept/postgres", "concept/sql"],
    }
    with pytest.raises(ValidationError):
        home_metadata("projects/missing", None)
    with pytest.raises(ValidationError):
        home_metadata("setup/../../escape", None)


def test_custom_roots_are_used():
    paths = VaultPaths(
        {"inbox": "Inbox", "project": "Projects", "research": "Learning", "resource": "Resources"}
    )
    assert paths.home_note("setup/helix", "Config") == "Resources/Setup/helix/config.md"
