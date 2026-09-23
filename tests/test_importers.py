"""Importer adapters: real export shapes, idempotency keys, honest attachments."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from personal_context_mcp.importers.adapters import (
    ChatGPTImporter,
    ClaudeImporter,
    GeminiImporter,
    GrokImporter,
    detect_importer,
)
from personal_context_mcp.importers.runner import derive_summary


def _zip(path: Path, members: dict[str, object]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data if isinstance(data, bytes) else json.dumps(data))
    return path


# ---------------------------------------------------------------- ChatGPT


CHATGPT_EXPORT = [
    {
        "title": "Shared memory architecture",
        "conversation_id": "conv-abc",
        "create_time": 1_760_000_000,
        "update_time": 1_760_001_000,
        "default_model_slug": "gpt-5",
        "current_node": "n3",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["n1"]},
            "n1": {
                "id": "n1",
                "parent": "root",
                "message": {
                    "id": "m1",
                    "author": {"role": "user"},
                    "content": {"parts": ["How should I share memory across assistants?"]},
                    "create_time": 1_760_000_010,
                    "metadata": {"attachments": [{"name": "diagram.png", "id": "file-1"}]},
                },
            },
            "n2": {
                "id": "n2",
                "parent": "n1",
                "message": {
                    "id": "m2",
                    "author": {"role": "assistant"},
                    "content": {"parts": ["Abandoned regeneration branch."]},
                    "create_time": 1_760_000_020,
                    "metadata": {"model_slug": "gpt-5"},
                },
            },
            "n3": {
                "id": "n3",
                "parent": "n1",
                "message": {
                    "id": "m3",
                    "author": {"role": "assistant"},
                    "content": {"parts": ["Use one MCP server as shared state."]},
                    "create_time": 1_760_000_030,
                    "metadata": {"model_slug": "gpt-5"},
                },
            },
        },
    }
]


def test_chatgpt_detects_and_parses(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "cg.zip", {"conversations.json": CHATGPT_EXPORT})
    importer = ChatGPTImporter()
    assert importer.detect(archive)

    conversations = list(importer.parse(archive))
    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation.provider == "chatgpt"
    assert conversation.provider_conversation_id == "conv-abc"
    assert conversation.title == "Shared memory architecture"
    assert conversation.created_at is not None


def test_chatgpt_follows_the_surviving_branch_only(tmp_path: Path) -> None:
    """A regenerated answer leaves an abandoned branch that must not be imported."""
    archive = _zip(tmp_path / "cg.zip", {"conversations.json": CHATGPT_EXPORT})
    conversation = next(iter(ChatGPTImporter().parse(archive)))

    texts = [m.content for m in conversation.messages]
    assert texts == [
        "How should I share memory across assistants?",
        "Use one MCP server as shared state.",
    ]
    assert "Abandoned regeneration branch." not in texts


def test_chatgpt_preserves_provenance_per_role(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "cg.zip", {"conversations.json": CHATGPT_EXPORT})
    conversation = next(iter(ChatGPTImporter().parse(archive)))
    assert conversation.messages[0].provenance == "user_statement"
    assert conversation.messages[1].provenance == "assistant_generated"


def test_chatgpt_attachment_present_in_archive_is_captured(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "cg.zip",
        {"conversations.json": CHATGPT_EXPORT, "diagram.png": b"\x89PNG realbytes"},
    )
    conversation = next(iter(ChatGPTImporter().parse(archive)))
    captured = [a for a in conversation.attachments if a.captured]
    assert len(captured) == 1
    assert captured[0].data == b"\x89PNG realbytes"


def test_chatgpt_attachment_absent_from_archive_is_only_referenced(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "cg.zip", {"conversations.json": CHATGPT_EXPORT})
    conversation = next(iter(ChatGPTImporter().parse(archive)))
    missing = [a for a in conversation.attachments if not a.captured]
    assert len(missing) == 1
    assert missing[0].filename == "diagram.png"
    assert missing[0].data is None


def test_chatgpt_reports_its_limitations(tmp_path: Path) -> None:
    assert ChatGPTImporter().limitations()


# ----------------------------------------------------------------- Claude


CLAUDE_EXPORT = [
    {
        "uuid": "claude-conv-1",
        "name": "Vault architecture",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T11:00:00Z",
        "chat_messages": [
            {
                "uuid": "cm1",
                "sender": "human",
                "text": "Should Obsidian hold the raw transcripts?",
                "created_at": "2026-09-01T10:00:00Z",
                "attachments": [
                    {"file_name": "notes.txt", "extracted_content": "Some extracted text."}
                ],
            },
            {
                "uuid": "cm2",
                "sender": "assistant",
                "text": "No - it is the curated layer, not the archive.",
                "created_at": "2026-09-01T10:01:00Z",
            },
        ],
    }
]


def test_claude_detects_and_parses(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "cl.zip", {"conversations.json": CLAUDE_EXPORT})
    importer = ClaudeImporter()
    assert importer.detect(archive)

    conversation = next(iter(importer.parse(archive)))
    assert conversation.provider == "claude"
    assert conversation.provider_conversation_id == "claude-conv-1"
    assert [m.role for m in conversation.messages] == ["user", "assistant"]


def test_claude_extracted_text_counts_as_captured(tmp_path: Path) -> None:
    """The text really is in the export, so it is genuinely captured."""
    archive = _zip(tmp_path / "cl.zip", {"conversations.json": CLAUDE_EXPORT})
    conversation = next(iter(ClaudeImporter().parse(archive)))
    attachment = conversation.attachments[0]
    assert attachment.captured
    assert attachment.data == b"Some extracted text."
    assert attachment.metadata["source"] == "extracted_content"


def test_claude_attachment_with_neither_bytes_nor_text_is_referenced(tmp_path: Path) -> None:
    export = json.loads(json.dumps(CLAUDE_EXPORT))
    export[0]["chat_messages"][0]["attachments"] = [{"file_name": "photo.jpg", "file_size": 1024}]
    archive = _zip(tmp_path / "cl.zip", {"conversations.json": export})
    conversation = next(iter(ClaudeImporter().parse(archive)))
    assert not conversation.attachments[0].captured


# ----------------------------------------------------------------- Gemini


GEMINI_ACTIVITY = [
    {
        "header": "Gemini Apps",
        "title": "Prompted How do I connect a custom MCP server?",
        "time": "2026-09-10T09:00:00Z",
        "products": ["Gemini Apps"],
        "details": [{"name": "Open Connected Apps and add a custom app."}],
    },
    {
        "header": "Gemini Apps",
        "title": "Prompted Does it need Spark?",
        "time": "2026-09-10T09:05:00Z",
        "products": ["Gemini Apps"],
        "details": [{"name": "No, a personal account is enough."}],
    },
    {
        "header": "Gemini Apps",
        "title": "Prompted A different day entirely",
        "time": "2026-09-11T09:00:00Z",
        "products": ["Gemini Apps"],
        "details": [{"name": "Answer."}],
    },
]


def test_gemini_groups_activity_by_day(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "ge.zip", {"Takeout/My Activity/Gemini Apps/My Activity.json": GEMINI_ACTIVITY}
    )
    importer = GeminiImporter()
    assert importer.detect(archive)

    conversations = list(importer.parse(archive))
    assert len(conversations) == 2
    assert conversations[0].provider_conversation_id == "takeout-day:2026-09-10"
    assert len(conversations[0].messages) == 4  # two prompts, two responses


def test_gemini_strips_the_takeout_prompt_prefix(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "ge.zip", {"Takeout/My Activity/Gemini Apps/My Activity.json": GEMINI_ACTIVITY}
    )
    conversation = next(iter(GeminiImporter().parse(archive)))
    assert conversation.messages[0].content == "How do I connect a custom MCP server?"


def test_gemini_import_key_is_stable_across_runs(tmp_path: Path) -> None:
    """Re-importing must not create a second copy of the same day."""
    archive = _zip(
        tmp_path / "ge.zip", {"Takeout/My Activity/Gemini Apps/My Activity.json": GEMINI_ACTIVITY}
    )
    first = [c.provider_conversation_id for c in GeminiImporter().parse(archive)]
    second = [c.provider_conversation_id for c in GeminiImporter().parse(archive)]
    assert first == second


def test_gemini_states_its_grouping_limitation(tmp_path: Path) -> None:
    limitations = " ".join(GeminiImporter().limitations())
    assert "no stable conversation identifier" in limitations
    assert "does not include images" in limitations


# ------------------------------------------------------------------- Grok


GROK_EXPORT = {
    "conversations": [
        {
            "conversation_id": "grok-1",
            "title": "Connector setup",
            "create_time": "2026-09-12T12:00:00Z",
            "messages": [
                {"id": "g1", "role": "user", "message": "Does Grok support static client ids?"},
                {"id": "g2", "role": "grok", "message": "Not in the connector form."},
            ],
        }
    ]
}


def test_grok_detects_and_parses(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "gr.zip", {"grok_conversations.json": GROK_EXPORT})
    importer = GrokImporter()
    assert importer.detect(archive)

    conversation = next(iter(importer.parse(archive)))
    assert conversation.provider_conversation_id == "grok-1"
    assert [m.role for m in conversation.messages] == ["user", "assistant"]


def test_grok_handles_an_alternative_shape(tmp_path: Path) -> None:
    """The format is less settled, so the adapter probes rather than assumes."""
    alternative = [
        {
            "id": "grok-2",
            "name": "Another shape",
            "turns": [
                {"sender": "user", "text": "hello"},
                {"sender": "bot", "text": "hi"},
            ],
        }
    ]
    archive = _zip(tmp_path / "gr2.zip", {"grok.json": alternative})
    conversation = next(iter(GrokImporter().parse(archive)))
    assert conversation.provider_conversation_id == "grok-2"
    assert len(conversation.messages) == 2


# ------------------------------------------------------------- dispatching


def test_detect_identifies_the_right_provider(tmp_path: Path) -> None:
    cg = _zip(tmp_path / "a.zip", {"conversations.json": CHATGPT_EXPORT})
    cl = _zip(tmp_path / "b.zip", {"conversations.json": CLAUDE_EXPORT})
    assert detect_importer(cg)[0] == "chatgpt"
    assert detect_importer(cl)[0] == "claude"


def test_detect_returns_nothing_for_an_unknown_archive(tmp_path: Path) -> None:
    unknown = _zip(tmp_path / "u.zip", {"random.txt": b"not an export"})
    assert detect_importer(unknown) == (None, None)


# ---------------------------------------------------------------- summary


def test_derived_summary_is_an_excerpt_not_an_invention(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "cg.zip", {"conversations.json": CHATGPT_EXPORT})
    conversation = next(iter(ChatGPTImporter().parse(archive)))
    summary = derive_summary(conversation)

    assert "Imported chatgpt conversation with 2 messages" in summary
    # Every claim in it is literally present in the transcript.
    assert "How should I share memory across assistants?" in summary
