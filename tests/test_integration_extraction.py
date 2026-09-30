"""Pass-2 deep extraction, with the Claude API replaced by a fake client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from conftest import requires_db
from personal_context_mcp.extraction.deep import (
    DeepExtractor,
    ExtractionPayload,
    extraction_candidates,
    render_transcript,
)
from personal_context_mcp.importers.runner import ImportRunner
from test_integration_backfill import CountingJev, _claude_zip, _conversation, _swap_classifier

pytestmark = requires_db

PAYLOAD = {
    "summary": "Chose Postgres as canonical memory and Obsidian as a rendered layer.",
    "project": None,
    "decisions": [
        {"statement": "Postgres is the canonical store.", "rationale": "Queryable history."}
    ],
    "facts": [
        {
            "content": "The person keeps their Obsidian vault in iCloud.",
            "kind": "fact",
            "subject": "obsidian-vault",
            "stated_by": "user",
        }
    ],
    "unfinished_actions": ["Write the MOC renderer."],
    "open_questions": ["Where should orphan notes go?"],
    "topics": ["MOC", "Obsidian"],
    "note_title": "Memory architecture",
    "note_markdown": "Postgres is canonical; Obsidian is rendered.\n\n## Key points\n\n- Raw history stays in Postgres.",
    "tags": ["Obsidian", "#memory", "agents/Hermes", "2026"],
}


class FakeMessages:
    def __init__(self, payload: dict[str, Any] | None, stop_reason: str = "end_turn") -> None:
        self.payload = payload
        self.stop_reason = stop_reason
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        text = json.dumps(self.payload) if self.payload is not None else ""
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            stop_details=SimpleNamespace(category="cyber") if self.payload is None else None,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
            model="claude-opus-5",
            _request_id="req_test",
        )


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.beta = SimpleNamespace(messages=messages)

    async def close(self) -> None:
        return None


async def _import(services, tmp_path: Path, conversations: list[dict[str, Any]]) -> None:
    _swap_classifier(services, CountingJev())  # answers deep_extraction = 0.9
    archive = _claude_zip(tmp_path / "e.zip", conversations)
    await ImportRunner(services).run(archive, provider="claude", store_archive=False)


async def test_payload_schema_is_strict_for_structured_outputs() -> None:
    schema = ExtractionPayload.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_long_transcripts_keep_both_ends_and_say_so() -> None:
    messages = [("user", "START " + "a" * 5000), ("assistant", "b" * 5000 + " END")]
    text, truncated = render_transcript(messages, 2000)
    assert truncated
    assert text.startswith("USER: START")
    assert text.endswith("END")
    assert "omitted" in text


async def test_extraction_writes_through_checkpoint_and_replaces_the_excerpt(
    services, tmp_path: Path
) -> None:
    await _import(services, tmp_path, [_conversation("a", "Where should memory live?")])
    candidates = await extraction_candidates(services, limit=10)
    assert len(candidates) == 1

    fake = FakeMessages(PAYLOAD)
    extractor = DeepExtractor(services, client=FakeClient(fake))  # type: ignore[arg-type]
    report = await extractor.run(
        [c.conversation_id for c in candidates],
        destinations={c.conversation_id: "wiki" for c in candidates},
    )

    assert report.extracted == 1 and report.failed == 0, report.errors
    request = fake.requests[0]
    assert request["fallbacks"] == "default"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "Vault section for the note: wiki" in request["messages"][0]["content"]
    assert report.notes == 1 and report.followups == 1

    summary = await services.db.fetchrow(
        "SELECT summary, provenance::text, open_questions FROM session_summaries"
    )
    assert summary["summary"] == PAYLOAD["summary"]  # replaced, not appended
    assert summary["provenance"] == "assistant_generated"
    assert summary["open_questions"] == ["Where should orphan notes go?"]

    assert await services.db.fetchval("SELECT count(*) FROM decisions") == 1
    fact = await services.db.fetchrow(
        "SELECT provenance::text FROM memories WHERE content LIKE '%iCloud%'"
    )
    assert fact["provenance"] == "user_statement"
    # Unfinished actions land on one shared list, marked with where they came from.
    item = await services.db.fetchrow(
        "SELECT i.text, i.notes, l.name FROM list_items i JOIN lists l ON l.id = i.list_id"
    )
    assert item["name"] == "From past chats"
    assert item["text"] == "Write the MOC renderer."
    assert "claude" in item["notes"]

    # The note itself, in the folder Jev chose, tagged, and queued for the vault.
    note = await services.db.fetchrow(
        "SELECT title, note_class, vault_path, metadata, content FROM notes"
    )
    assert note["title"] == "Memory architecture"
    assert note["note_class"] == "wiki"
    assert note["vault_path"].startswith("40-Wiki/")
    assert note["metadata"]["tags"] == ["obsidian", "memory", "agents/hermes"]
    assert "Postgres is canonical" in note["content"]
    jobs = await services.db.fetch("SELECT job_type FROM sync_jobs ORDER BY created_at")
    assert {"write_note", "write_list"} <= {j["job_type"] for j in jobs}

    meta = await services.db.fetchval("SELECT metadata->'extraction' FROM conversations")
    assert meta["status"] == "extracted"
    assert meta["topics"] == ["MOC", "Obsidian"]
    # Done once; not offered again without --redo.
    assert await extraction_candidates(services, limit=10) == []
    assert len(await extraction_candidates(services, limit=10, redo=True)) == 1


async def test_rerunning_extraction_does_not_duplicate(services, tmp_path: Path) -> None:
    await _import(services, tmp_path, [_conversation("a", "Where should memory live?")])
    ids = [c.conversation_id for c in await extraction_candidates(services, limit=10)]
    extractor = DeepExtractor(services, client=FakeClient(FakeMessages(PAYLOAD)))  # type: ignore[arg-type]
    await extractor.run(ids)
    await extractor.run(ids)
    assert await services.db.fetchval("SELECT count(*) FROM decisions") == 1
    assert await services.db.fetchval("SELECT count(*) FROM session_summaries") == 1
    assert await services.db.fetchval("SELECT summary FROM session_summaries") == PAYLOAD["summary"]


async def test_sensitive_conversations_are_never_sent(services, tmp_path: Path) -> None:
    await _import(services, tmp_path, [_conversation("s", "my key is sk-ant-api03-" + "k" * 40)])
    assert await extraction_candidates(services, limit=10) == []

    conversation_id = await services.db.fetchval("SELECT id FROM conversations")
    fake = FakeMessages(PAYLOAD)
    extractor = DeepExtractor(services, client=FakeClient(fake))  # type: ignore[arg-type]
    report = await extractor.run([conversation_id])
    assert fake.requests == []
    assert report.extracted == 0
    assert "kept local" in report.errors[0]


async def test_a_refusal_is_recorded_not_written(services, tmp_path: Path) -> None:
    await _import(services, tmp_path, [_conversation("r", "Something declined.")])
    ids = [c.conversation_id for c in await extraction_candidates(services, limit=10)]
    extractor = DeepExtractor(
        services,
        client=FakeClient(FakeMessages(None, stop_reason="refusal")),  # type: ignore[arg-type]
    )
    report = await extractor.run(ids)
    assert report.refused == 1
    meta = await services.db.fetchval("SELECT metadata->'extraction' FROM conversations")
    assert meta["status"] == "refused"
    assert meta["refusal_category"] == "cyber"
    assert await services.db.fetchval("SELECT count(*) FROM decisions") == 0


async def test_same_subject_notes_merge_and_projects_are_created(services, tmp_path: Path) -> None:
    await _import(
        services,
        tmp_path,
        [_conversation("a", "Where should memory live?"), _conversation("b", "More on memory.")],
    )
    ids = [c.conversation_id for c in await extraction_candidates(services, limit=10)]
    payload = {**PAYLOAD, "project": "Personal Context"}
    extractor = DeepExtractor(services, client=FakeClient(FakeMessages(payload)))  # type: ignore[arg-type]
    await extractor.run(ids, destinations=dict.fromkeys(ids, "project"))

    notes = await services.db.fetch("SELECT title, content, vault_path FROM notes")
    assert len(notes) == 1  # same title: appended, not duplicated
    assert notes[0]["content"].count("Postgres is canonical") == 1  # identical text not repeated
    assert notes[0]["vault_path"] == "20-Projects/personal-context/memory-architecture.md"
    assert await services.db.fetchval("SELECT count(*) FROM projects") == 1


async def test_list_changes_are_queued_for_the_vault(services) -> None:
    list_id, _ = await services.lists.create(name="Groceries", list_type="shopping")
    item_id, _ = await services.lists.add_item(list_id=list_id, text="Oat milk")
    await services.lists.complete_item(item_id)
    jobs = await services.db.fetch(
        "SELECT job_type, vault_path, status FROM sync_jobs WHERE object_id = $1", list_id
    )
    # One open job per file: each change supersedes the previous one.
    assert [j["status"] for j in jobs].count("approved") == 1
    assert jobs[0]["vault_path"] == "60-Resources/Lists/groceries.md"
