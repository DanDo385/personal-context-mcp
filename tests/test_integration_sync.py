"""Integration: sync job queue, worker API, classification persistence, audit."""

from __future__ import annotations

import httpx

from conftest import requires_db
from personal_context_mcp.server import create_app

pytestmark = requires_db


# --------------------------------------------------------------- sync queue


async def test_enqueue_validates_the_path(services) -> None:
    from personal_context_mcp.errors import PathSecurityError

    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    try:
        await services.sync_queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_id,
            vault_path="../../escape.md",
            payload={},
        )
    except PathSecurityError:
        pass
    else:
        raise AssertionError("a traversal path was accepted into the queue")

    assert await services.db.fetchval("SELECT count(*) FROM sync_jobs") == 0


async def test_a_newer_job_supersedes_an_open_one_for_the_same_path(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    first = await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="00-Inbox/a-note.md", payload={"v": 1},
    )
    second = await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="00-Inbox/a-note.md", payload={"v": 2},
    )
    assert first != second

    statuses = {
        r["id"]: r["status"]
        for r in await services.db.fetch("SELECT id, status FROM sync_jobs")
    }
    assert statuses[first] == "cancelled"
    assert statuses[second] == "approved"


async def test_lease_is_exclusive(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="00-Inbox/a-note.md", payload={},
    )

    first = await services.sync_queue.lease(worker_id="w1")
    second = await services.sync_queue.lease(worker_id="w2")
    assert len(first) == 1
    assert second == []


async def test_completing_a_job_records_an_event(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="00-Inbox/a-note.md", payload={},
    )
    job = (await services.sync_queue.lease(worker_id="w1"))[0]
    await services.sync_queue.complete(job["id"], worker_id="w1", written_sha256="abc")

    assert await services.db.fetchval(
        "SELECT status FROM sync_jobs WHERE id = $1", job["id"]
    ) == "done"
    assert await services.db.fetchval(
        "SELECT count(*) FROM sync_events WHERE sync_job_id = $1 AND event_type = 'acked'",
        job["id"],
    ) == 1


async def test_a_conflict_is_terminal_not_retried(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="00-Inbox/a-note.md", payload={},
    )
    job = (await services.sync_queue.lease(worker_id="w1"))[0]
    await services.sync_queue.fail(
        job["id"], worker_id="w1", error="file changed on disk", conflict=True
    )

    assert await services.db.fetchval(
        "SELECT status FROM sync_jobs WHERE id = $1", job["id"]
    ) == "conflict"
    # A conflict must not be re-leased and overwrite the human's edit.
    assert await services.sync_queue.lease(worker_id="w2") == []


async def test_an_ordinary_failure_returns_to_the_queue(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="00-Inbox/a-note.md", payload={},
    )
    job = (await services.sync_queue.lease(worker_id="w1"))[0]
    await services.sync_queue.fail(job["id"], worker_id="w1", error="disk was busy")

    assert len(await services.sync_queue.lease(worker_id="w2")) == 1


# --------------------------------------------------------------- worker API


async def _worker_client(services):
    app = create_app(services.settings)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-worker-key": services.settings.worker_api_key.get_secret_value()},
    )


async def test_worker_lease_returns_rendered_markdown(services) -> None:
    note_id, _, _ = await services.notes.upsert(
        title="MCP OAuth", content="Grok requires DCR.", note_class="research"
    )
    await services.sync_queue.enqueue(
        job_type="write_note", object_type="note", object_id=note_id,
        vault_path="30-Research/mcp-oauth.md", payload={},
    )

    async with await _worker_client(services) as client:
        response = await client.post("/worker/jobs/lease", json={"worker_id": "w1"})
        assert response.status_code == 200
        jobs = response.json()["jobs"]

    assert len(jobs) == 1
    # Rendering happens server-side so the worker stays a dumb file writer.
    assert jobs[0]["vault_path"] == "30-Research/mcp-oauth.md"
    assert "Grok requires DCR." in jobs[0]["content"]
    assert f"pc_id: {note_id}" in jobs[0]["content"]


async def test_worker_can_push_a_human_note_edit_back(services) -> None:
    note_id, _, version = await services.notes.upsert(
        title="A note", content="original", vault_path="00-Inbox/a-note.md"
    )

    async with await _worker_client(services) as client:
        response = await client.post(
            "/worker/edits",
            json={
                "worker_id": "w1",
                "edits": [
                    {
                        "kind": "note",
                        "vault_path": "00-Inbox/a-note.md",
                        "content": "original, plus my own thinking",
                        "sha256": "deadbeef",
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["applied"][0]["changed"] is True

    note = await services.notes.get(str(note_id))
    assert note.content == "original, plus my own thinking"
    assert note.version == version + 1


async def test_a_ticked_checkbox_flows_back_to_postgres(services) -> None:
    """Acceptance: a box ticked by hand in Obsidian updates canonical state."""
    list_id, _ = await services.lists.create(name="Personal AI tasks", list_type="task")
    item_id, _ = await services.lists.add_item(
        list_id=list_id, text="Test the Gemini connector"
    )

    async with await _worker_client(services) as client:
        response = await client.post(
            "/worker/edits",
            json={
                "worker_id": "w1",
                "edits": [
                    {
                        "kind": "list_items",
                        "vault_path": "60-Resources/Lists/personal-ai-tasks.md",
                        "sha256": "abc",
                        "items": [{"id": str(item_id), "checked": True}],
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["applied"][0]["changed"] == 1

    items = await services.lists.items("personal-ai-tasks", status="done")
    assert len(items) == 1
    assert items[0].id == item_id

    # The change is attributed to the human, not to a model.
    audit = await services.db.fetchrow(
        "SELECT provenance, approval_status, tool FROM audit_log WHERE object_id = $1",
        item_id,
    )
    assert audit["provenance"] == "user_statement"
    assert audit["approval_status"] == "user_approved"
    assert audit["tool"] == "obsidian_sync"


async def test_an_unmarked_line_is_reported_not_matched_by_text(services) -> None:
    list_id, _ = await services.lists.create(name="Tasks", list_type="task")
    await services.lists.add_item(list_id=list_id, text="Something")

    async with await _worker_client(services) as client:
        response = await client.post(
            "/worker/edits",
            json={
                "worker_id": "w1",
                "edits": [
                    {
                        "kind": "list_items",
                        "vault_path": "60-Resources/Lists/tasks.md",
                        "sha256": "abc",
                        "items": [{"id": None, "checked": True, "text": "Something"}],
                    }
                ],
            },
        )

    applied = response.json()["applied"][0]
    assert applied["changed"] == 0
    assert applied["unmatched"] == ["Something"]
    # Nothing was completed on a guess.
    assert len(await services.lists.items("tasks", status="open")) == 1


# ------------------------------------------------- classification + audit


async def test_every_classification_is_persisted(services) -> None:
    from personal_context_mcp.auth.principal import Principal
    from personal_context_mcp.models import CheckpointFact

    await services.sessions.checkpoint(
        provider="claude",
        principal=Principal(subject="tester@example.com", provider="claude"),
        provider_conversation_id="conv-classify",
        summary="Something worth classifying.",
        facts=[CheckpointFact(content="A durable fact.")],
    )

    event = await services.db.fetchrow(
        "SELECT * FROM classification_events ORDER BY created_at DESC LIMIT 1"
    )
    assert event is not None
    assert event["object_type"] == "checkpoint"
    assert event["classifier"] in ("jev", "rules")
    assert event["questions_version"]
    assert event["input_hash"] == "" or len(event["input_hash"]) == 64
    assert event["answers"]
    assert event["action_taken"]


async def test_audit_records_who_changed_what(services) -> None:
    from personal_context_mcp.auth.principal import Principal

    principal = Principal(
        subject="tester@example.com", client_id="cl-1",
        client_name="Claude", provider="claude",
    )
    await services.sessions.checkpoint(
        provider="claude",
        principal=principal,
        provider_conversation_id="conv-audit",
        summary="A summary.",
        model="claude-opus-5",
    )

    row = await services.db.fetchrow(
        "SELECT * FROM audit_log WHERE tool = 'session_checkpoint' ORDER BY created_at DESC LIMIT 1"
    )
    assert row["principal"] == "tester@example.com"
    assert row["provider"] == "claude"
    assert row["model"] == "claude-opus-5"
    assert row["object_type"] == "conversation"
    assert row["classification_event_id"] is not None


async def test_a_degraded_classification_queues_reclassification(services) -> None:
    from personal_context_mcp.auth.principal import Principal
    from personal_context_mcp.classifier.rules import RulesClassifier

    services.classification._classifier = RulesClassifier(degraded=True, error="forced")

    result = await services.sessions.checkpoint(
        provider="claude",
        principal=Principal(subject="tester@example.com", provider="claude"),
        provider_conversation_id="conv-degraded",
        summary="Classified while the classifier was down.",
    )

    assert result.classification["degraded"] is True
    assert any("classifier was unavailable" in n for n in result.notes)
    # The data itself was still saved in full.
    assert result.session_summary_id is not None
    assert await services.db.fetchval(
        "SELECT count(*) FROM reclassification_queue WHERE object_type = 'checkpoint'"
    ) == 1


async def test_review_reclassify_records_the_override(services) -> None:
    await services.projects.create(name="Alpha")
    conversation_id, _ = await services.sessions.ensure_conversation(
        provider="claude", provider_conversation_id="conv-review"
    )
    event_id = await services.db.fetchval(
        """
        INSERT INTO classification_events (
            object_type, object_id, classifier, questions_version, input_hash,
            answers, action_taken
        ) VALUES ('conversation', $1, 'jev', 'v1', 'h', '{}'::jsonb, 'review')
        RETURNING id
        """,
        conversation_id,
    )
    review_id = await services.classification.queue_review(
        object_type="conversation",
        object_id=conversation_id,
        reason="ambiguous_project",
        question="Which project?",
        classification_event_id=event_id,
    )

    await services.review.resolve(
        review_id, status="reclassified", resolution={"project": "alpha"},
        overridden=True, override_note="it was Alpha all along",
    )

    event = await services.db.fetchrow(
        "SELECT overridden_by_user, override_note FROM classification_events WHERE id = $1",
        event_id,
    )
    assert event["overridden_by_user"] is True
    assert event["override_note"] == "it was Alpha all along"
