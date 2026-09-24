"""Historical backfill: sensitivity screening, the classification cache,
concurrent import and reclassification from stored messages."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from conftest import requires_db
from personal_context_mcp.classifier.base import Answer, ClassificationResult
from personal_context_mcp.classifier.questions import QUESTIONS_VERSION
from personal_context_mcp.classifier.service import ClassificationService
from personal_context_mcp.importers.reclassify import (
    reclassify_conversations,
    stale_conversations,
)
from personal_context_mcp.importers.runner import ImportRunner

pytestmark = requires_db


class CountingJev:
    """Stands in for Jev: counts calls and answers with fixed probabilities."""

    name = "jev"

    def __init__(self, *, degraded: bool = False) -> None:
        self.calls = 0
        self.states: list[dict[str, Any]] = []
        self.degraded = degraded

    async def classify_checkpoint(
        self, state: dict[str, Any], candidates: dict[str, str]
    ) -> ClassificationResult:
        self.calls += 1
        self.states.append(state)
        return ClassificationResult(
            answers={
                "save_durable_memory": Answer("noul", 0.2),
                "deep_extraction": Answer("noul", 0.9),
                "destination": Answer(
                    "choice", "research", confidence=0.97, probabilities={"research": 0.97}
                ),
                "importance": Answer("score", 3, confidence=0.9),
            },
            classifier="rules" if self.degraded else "jev",
            model="jev-test",
            questions_version=QUESTIONS_VERSION,
            input_hash=f"h{self.calls}",
            degraded=self.degraded,
        )

    classify_conversation = classify_checkpoint


def _swap_classifier(services, fake) -> ClassificationService:
    service = ClassificationService(services.db, fake, services.settings)
    services.classification = service
    return service


def _claude_zip(path: Path, conversations: list[dict[str, Any]]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations))
    return path


def _conversation(uuid: str, text: str) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": f"Chat {uuid}",
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-01T11:00:00Z",
        "chat_messages": [
            {"uuid": f"{uuid}-1", "sender": "human", "text": text},
            {"uuid": f"{uuid}-2", "sender": "assistant", "text": "Understood."},
        ],
    }


STATE = {
    "title": "Shared memory design",
    "provider": "claude",
    "summary": "Decided Postgres is canonical and Obsidian is rendered.",
    "sensitivity": "normal",
}


async def test_identical_input_is_answered_from_the_cache(services) -> None:
    fake = CountingJev()
    service = _swap_classifier(services, fake)

    first, routing1, _ = await service.classify_checkpoint(dict(STATE))
    second, routing2, event2 = await service.classify_checkpoint(dict(STATE))

    assert fake.calls == 1
    assert routing1.as_json() == routing2.as_json()
    assert second.answers_json() == first.answers_json()
    meta = await services.db.fetchval(
        "SELECT metadata FROM classification_events WHERE id = $1", event2
    )
    assert meta["cache_hit"] is True


async def test_cache_can_be_bypassed_and_changes_with_input(services) -> None:
    fake = CountingJev()
    service = _swap_classifier(services, fake)

    await service.classify_checkpoint(dict(STATE))
    await service.classify_checkpoint(dict(STATE), use_cache=False)
    await service.classify_checkpoint({**STATE, "summary": "Something else entirely."})
    assert fake.calls == 3


async def test_degraded_results_are_never_cached(services) -> None:
    fake = CountingJev(degraded=True)
    service = _swap_classifier(services, fake)

    await service.classify_checkpoint(dict(STATE))
    await service.classify_checkpoint(dict(STATE))
    assert fake.calls == 2


async def test_import_screens_sensitivity_before_classifying(services, tmp_path: Path) -> None:
    fake = CountingJev()
    _swap_classifier(services, fake)
    archive = _claude_zip(
        tmp_path / "export.zip",
        [
            _conversation("plain", "How do I index pgvector?"),
            _conversation("secret", "use this key sk-ant-api03-" + "x" * 40),
            _conversation("health", "My therapist suggested I sleep more."),
        ],
    )

    report = await ImportRunner(services).run(archive, provider="claude", store_archive=False)

    assert report.conversations_imported == 3
    assert report.conversations_screened_sensitive == 2
    levels = {
        row["provider_conversation_id"]: row["sensitivity"]
        for row in await services.db.fetch(
            "SELECT provider_conversation_id, sensitivity::text FROM conversations"
        )
    }
    assert levels == {"plain": "normal", "secret": "highly_sensitive", "health": "private"}
    # The classifier was told, so its privacy routing can keep it local.
    sent = {state["title"]: state["sensitivity"] for state in fake.states}
    assert sent["Chat secret"] == "highly_sensitive"


async def test_concurrent_import_imports_everything_once(services, tmp_path: Path) -> None:
    fake = CountingJev()
    _swap_classifier(services, fake)
    services.settings.import_concurrency = 5
    archive = _claude_zip(
        tmp_path / "export.zip",
        [_conversation(f"c{i}", f"Question number {i} about MCP transports.") for i in range(23)],
    )

    report = await ImportRunner(services).run(archive, provider="claude", store_archive=False)
    again = await ImportRunner(services).run(archive, provider="claude", store_archive=False)

    assert report.errors == []
    assert report.conversations_imported == 23
    assert report.summaries_created == 23
    assert report.deep_extraction_candidates == 23
    assert again.conversations_imported == 0
    assert await services.db.fetchval("SELECT count(*) FROM conversations") == 23
    assert await services.db.fetchval("SELECT count(*) FROM session_summaries") == 23


async def test_reclassify_finds_stale_versions_and_keeps_summaries(
    services, tmp_path: Path
) -> None:
    fake = CountingJev()
    _swap_classifier(services, fake)
    archive = _claude_zip(tmp_path / "e.zip", [_conversation("one", "Design the MOC renderer.")])
    await ImportRunner(services).run(archive, provider="claude", store_archive=False)
    conversation_id = await services.db.fetchval("SELECT id FROM conversations")

    assert await stale_conversations(services, limit=10) == []

    # Simulate deep extraction having written a better summary, then a
    # question-set change making the old classification stale.
    await services.db.execute(
        """
        UPDATE session_summaries
        SET summary = 'Extracted: the MOC renderer is deterministic Python.',
            summary_sha256 = 'x', open_questions = '["Where do orphans go?"]'::jsonb
        WHERE conversation_id = $1
        """,
        conversation_id,
    )
    await services.db.execute("UPDATE classification_events SET questions_version = 'old'")
    assert await stale_conversations(services, limit=10) == [conversation_id]

    report = await reclassify_conversations(services, [conversation_id])

    assert report.errors == []
    row = await services.db.fetchrow(
        "SELECT summary, open_questions FROM session_summaries WHERE conversation_id = $1",
        conversation_id,
    )
    assert row["summary"] == "Extracted: the MOC renderer is deterministic Python."
    assert row["open_questions"] == ["Where do orphans go?"]
    # It classified the stored summary, not a fresh excerpt.
    assert fake.states[-1]["summary"].startswith("Extracted:")
    assert await stale_conversations(services, limit=10) == []


@pytest.mark.parametrize("level", ["private", "highly_sensitive"])
async def test_import_default_sensitivity_applies_to_everything(
    services, tmp_path: Path, level: str
) -> None:
    fake = CountingJev()
    _swap_classifier(services, fake)
    services.settings.import_default_sensitivity = level  # type: ignore[assignment]
    archive = _claude_zip(tmp_path / "e.zip", [_conversation("p", "How do I index pgvector?")])

    await ImportRunner(services).run(archive, provider="claude", store_archive=False)

    stored = await services.db.fetchval("SELECT sensitivity::text FROM conversations")
    assert stored == level
