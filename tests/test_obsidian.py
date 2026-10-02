"""Vault rendering, list round-tripping, and safe writes."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from obsidian_sync.vault import Vault, note_id_of, parse_list_items
from personal_context_mcp.errors import PathSecurityError
from personal_context_mcp.obsidian.render import item_marker, render_list, render_moc, render_note
from personal_context_mcp.util import sha256_bytes

ROOTS = {"inbox": "00-Inbox", "project": "20-Projects", "resource": "60-Resources"}


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for name in ROOTS.values():
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
def vault(vault_dir: Path) -> Vault:
    return Vault(vault_dir, ROOTS, max_bytes=1_000_000, dry_run=False)


# ---------------------------------------------------------------- rendering


def test_rendered_list_carries_stable_item_ids() -> None:
    list_id, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    markdown = render_list(
        {
            "id": list_id,
            "name": "Personal AI tasks",
            "list_type": "task",
            "description": None,
            "project_slug": "personal-ai",
            "updated_at": None,
        },
        [
            {
                "id": a,
                "text": "Test the Gemini connector",
                "status": "open",
                "priority": 1,
                "due_date": None,
                "notes": None,
            },
            {
                "id": b,
                "text": "Ship the MCP server",
                "status": "done",
                "priority": 0,
                "due_date": None,
                "notes": None,
            },
        ],
    )
    assert item_marker(a) in markdown
    assert item_marker(b) in markdown
    assert "- [ ] Test the Gemini connector" in markdown
    assert "- [x] Ship the MCP server" in markdown


def test_round_trip_of_a_rendered_list() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    markdown = render_list(
        {
            "id": uuid.uuid4(),
            "name": "L",
            "list_type": "task",
            "description": None,
            "project_slug": None,
            "updated_at": None,
        },
        [
            {
                "id": a,
                "text": "one",
                "status": "open",
                "priority": 0,
                "due_date": None,
                "notes": None,
            },
            {
                "id": b,
                "text": "two",
                "status": "done",
                "priority": 0,
                "due_date": None,
                "notes": None,
            },
        ],
    )
    items = parse_list_items(markdown)
    by_id = {i["id"]: i for i in items}
    assert by_id[str(a)]["checked"] is False
    assert by_id[str(b)]["checked"] is True


def test_a_human_ticking_a_box_is_detected() -> None:
    item_id = uuid.uuid4()
    line = f"- [x] Test the Gemini connector {item_marker(item_id)}"
    parsed = parse_list_items(line)
    assert parsed == [{"id": str(item_id), "checked": True, "text": "Test the Gemini connector"}]


def test_an_item_without_a_marker_is_reported_not_guessed() -> None:
    """Matching a hand-added line by text could tick the wrong item."""
    parsed = parse_list_items("- [x] A line someone typed by hand")
    assert parsed[0]["id"] is None
    assert parsed[0]["text"] == "A line someone typed by hand"


def test_rendered_list_omits_dropped_items_and_tags_its_type() -> None:
    markdown = render_list(
        {
            "id": uuid.uuid4(),
            "name": "Todo",
            "list_type": "task",
            "description": None,
            "project_slug": None,
            "updated_at": None,
        },
        [
            {
                "id": uuid.uuid4(),
                "text": "Still open",
                "status": "open",
                "priority": 0,
                "due_date": None,
                "notes": None,
            },
            {
                "id": uuid.uuid4(),
                "text": "Thrown away",
                "status": "dropped",
                "priority": 0,
                "due_date": None,
                "notes": None,
            },
        ],
    )
    assert "type/list" in markdown
    assert "Still open" in markdown
    assert "Thrown away" not in markdown


def test_note_and_map_frontmatter_carry_type_tags() -> None:
    project = render_note(
        {
            "id": uuid.uuid4(),
            "title": "A project",
            "content": "Body.",
            "note_class": "project",
            "metadata": {"tags": ["already/set"]},
            "source_provider": "claude",
        }
    )
    index = render_note(
        {
            "id": uuid.uuid4(),
            "title": "ChatGPT",
            "content": "Each row is one conversation.",
            "note_class": "session_index",
        }
    )
    research = render_note(
        {
            "id": uuid.uuid4(),
            "title": "A finding",
            "content": "Body.",
            "note_class": "research",
        }
    )
    moc = render_moc(
        {"id": uuid.uuid4(), "title": "Home", "description": "Start here."},
        {"reference": [("chatgpt", "ChatGPT")]},
        section_titles={"reference": "Reference"},
        lists=[("todo", "Todo")],
        children=[("revamp", "Revamp")],
    )
    assert "type/project" in project
    assert "already/set" in project
    assert "from/claude" in project
    assert "type/session-index" in index
    assert "type/project" not in research
    assert "type/moc" in moc
    assert "## Lists" in moc
    assert "[[todo|Todo]]" in moc
    assert "[[chatgpt|ChatGPT]]" in moc


def test_note_frontmatter_carries_identity_and_provenance() -> None:
    note_id = uuid.uuid4()
    markdown = render_note(
        {
            "id": note_id,
            "title": "MCP OAuth",
            "content": "Body text.",
            "note_class": "research",
            "project_slug": "personal-ai",
            "source_provider": "claude",
            "source_conversation_id": None,
            "provenance": "assistant_generated",
            "version": 3,
            "updated_at": None,
            "citations": [{"title": "Spec", "url": "https://example.com"}],
        }
    )
    assert f"pc_id: {note_id}" in markdown
    assert "provenance: assistant_generated" in markdown
    assert note_id_of(markdown) == str(note_id)
    assert "[Spec](https://example.com)" in markdown


# ------------------------------------------------------------------ writing


def test_write_creates_directories_and_file(vault: Vault, vault_dir: Path) -> None:
    outcome = vault.write("20-Projects/alpha/note.md", b"hello", expected_sha256=None)
    assert outcome.written
    assert (vault_dir / "20-Projects/alpha/note.md").read_bytes() == b"hello"
    assert outcome.sha256 == sha256_bytes(b"hello")


def test_identical_content_is_a_no_op(vault: Vault) -> None:
    vault.write("00-Inbox/n.md", b"same", expected_sha256=None)
    outcome = vault.write("00-Inbox/n.md", b"same", expected_sha256=None)
    assert outcome.written
    assert outcome.reason == "already up to date"


def test_human_edit_is_never_silently_overwritten(vault: Vault, vault_dir: Path) -> None:
    target = vault_dir / "00-Inbox/n.md"
    vault.write("00-Inbox/n.md", b"version one", expected_sha256=None)

    # A person edits the file in Obsidian.
    target.write_bytes(b"version one, with my own additions")

    outcome = vault.write(
        "00-Inbox/n.md", b"version two", expected_sha256=sha256_bytes(b"version one")
    )
    assert outcome.conflict
    assert not outcome.written
    assert "human edit" in outcome.reason
    assert target.read_bytes() == b"version one, with my own additions"


def test_write_with_a_matching_expected_hash_proceeds(vault: Vault) -> None:
    vault.write("00-Inbox/n.md", b"v1", expected_sha256=None)
    outcome = vault.write("00-Inbox/n.md", b"v2", expected_sha256=sha256_bytes(b"v1"))
    assert outcome.written


def test_traversal_is_refused_at_the_worker_too(vault: Vault) -> None:
    """The server validated it; the worker validates it again anyway."""
    outcome = vault.write("../../escape.md", b"x", expected_sha256=None)
    assert not outcome.written
    assert "path refused" in outcome.reason


def test_unknown_root_is_refused(vault: Vault) -> None:
    outcome = vault.write("99-NotConfigured/n.md", b"x", expected_sha256=None)
    assert not outcome.written
    assert "path refused" in outcome.reason


def test_oversized_write_is_refused(vault_dir: Path) -> None:
    small = Vault(vault_dir, ROOTS, max_bytes=10, dry_run=False)
    outcome = small.write("00-Inbox/n.md", b"x" * 100, expected_sha256=None)
    assert not outcome.written
    assert "exceeds" in outcome.reason


def test_dry_run_writes_nothing(vault_dir: Path) -> None:
    dry = Vault(vault_dir, ROOTS, max_bytes=1_000_000, dry_run=True)
    outcome = dry.write("00-Inbox/n.md", b"x", expected_sha256=None)
    assert not outcome.written
    assert outcome.reason == "dry run"
    assert not (vault_dir / "00-Inbox/n.md").exists()


def test_symlink_inside_the_vault_is_not_followed(vault_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault_dir / "20-Projects" / "sneaky").symlink_to(outside, target_is_directory=True)

    vault = Vault(vault_dir, ROOTS, max_bytes=1_000_000, dry_run=False)
    outcome = vault.write("20-Projects/sneaky/note.md", b"x", expected_sha256=None)
    assert not outcome.written
    assert not (outside / "note.md").exists()


def test_scan_detects_a_changed_file(vault: Vault, vault_dir: Path) -> None:
    vault.write("00-Inbox/n.md", b"original", expected_sha256=None)
    known = {"00-Inbox/n.md": sha256_bytes(b"original")}
    assert vault.scan_for_edits(known) == []

    (vault_dir / "00-Inbox/n.md").write_bytes(b"edited by hand")
    edits = vault.scan_for_edits(known)
    assert len(edits) == 1
    assert edits[0]["kind"] == "note"
    assert edits[0]["content"] == "edited by hand"


def test_vault_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Vault(tmp_path / "nope", ROOTS, max_bytes=1000, dry_run=False)


def test_resolve_refuses_absolute_paths(vault: Vault) -> None:
    with pytest.raises(PathSecurityError):
        vault.resolve("/etc/passwd")
