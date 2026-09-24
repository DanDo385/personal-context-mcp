"""The wiki graph: links, Maps of Content, rendering and vault round trips."""

from __future__ import annotations

from typing import Any

from conftest import requires_db
from personal_context_mcp.classifier.base import Answer, ClassificationResult
from personal_context_mcp.classifier.questions import QUESTIONS_VERSION
from personal_context_mcp.classifier.service import ClassificationService
from personal_context_mcp.obsidian.render import GENERATED_START, extract_note_body
from personal_context_mcp.wiki.service import WikiService

pytestmark = requires_db


class ScriptedJev:
    """Answers link questions from a {target title: should_link} script."""

    name = "jev"

    def __init__(self, links: dict[str, float], moc: tuple[str, float] = ("none", 0.9)):
        self.links = links
        self.moc = moc
        self.link_calls: list[tuple[str, str]] = []
        self.moc_calls = 0

    def _result(self, answers: dict[str, Answer]) -> ClassificationResult:
        return ClassificationResult(
            answers=answers, classifier="jev", model="jev-test", questions_version=QUESTIONS_VERSION
        )

    async def classify_note_link(self, state: dict[str, Any]) -> ClassificationResult:
        self.link_calls.append((state["source_title"], state["target_title"]))
        p = self.links.get(state["target_title"], 0.05)
        return self._result(
            {
                "should_link": Answer("noul", p),
                "navigation_value": Answer("noul", p),
                "relation": Answer(
                    "choice", "prerequisite", confidence=0.9, probabilities={"prerequisite": 0.9}
                ),
            }
        )

    async def classify_moc(
        self, state: dict[str, Any], candidates: dict[str, str]
    ) -> ClassificationResult:
        self.moc_calls += 1
        slug, confidence = self.moc
        return self._result(
            {
                "moc": Answer("choice", slug, confidence=confidence),
                "section": Answer("choice", "fundamentals", confidence=0.9),
            }
        )


async def _note(services, title: str, content: str) -> Any:
    note_id, _, _ = await services.notes.upsert(title=title, content=content, note_class="wiki")
    path = services.vault_paths.note(note_class="wiki", title=title)
    await services.db.execute("UPDATE notes SET vault_path = $2 WHERE id = $1", note_id, path)
    return note_id


def _use(services, fake: ScriptedJev) -> None:
    services.classification = ClassificationService(services.db, fake, services.settings)


async def test_a_title_mention_links_without_asking_jev(services) -> None:
    fake = ScriptedJev({})
    _use(services, fake)
    source = await _note(services, "MCP OAuth", "Clients use PKCE during the flow.")
    target = await _note(services, "PKCE", "Proof Key for Code Exchange.")

    report = await WikiService(services).build_links()

    edge = await services.db.fetchrow(
        "SELECT status, source FROM note_edges WHERE source_note_id = $1 AND target_note_id = $2",
        source,
        target,
    )
    assert edge["status"] == "accepted" and edge["source"] == "title_mention"
    assert ("MCP OAuth", "PKCE") not in fake.link_calls
    assert report.title_links == 1


async def test_jev_confidence_bands_decide_links(services) -> None:
    fake = ScriptedJev({"Token audience": 0.97, "Refresh tokens": 0.7, "Cooking rice": 0.05})
    _use(services, fake)
    await _note(services, "Token audience", "The audience claim of an OAuth access token.")
    await _note(services, "Refresh tokens", "Long-lived OAuth tokens that mint access tokens.")
    await _note(services, "Cooking rice", "Rice needs water and heat.")
    # Created last, so it is the most recent note and the one limit=1 takes.
    await _note(
        services, "OAuth flows", "Authorization server, the audience of tokens, token refresh, rice"
    )

    service = WikiService(services)
    await service.build_links(limit=1)

    status = {
        row["title"]: row["status"]
        for row in await services.db.fetch(
            """
            SELECT t.title, e.status FROM note_edges e JOIN notes t ON t.id = e.target_note_id
            JOIN notes s ON s.id = e.source_note_id WHERE s.title = 'OAuth flows'
            """
        )
    }
    assert status == {
        "Token audience": "accepted",
        "Refresh tokens": "review",
        "Cooking rice": "rejected",
    }
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM review_queue WHERE object_type = 'note_link'"
        )
        == 1
    )

    # Decided pairs are not asked again.
    asked = len(fake.link_calls)
    await service.build_links(limit=1, redo=False)
    assert len(fake.link_calls) == asked


async def test_a_human_decision_is_never_overwritten(services) -> None:
    fake = ScriptedJev({"B note": 0.99})
    _use(services, fake)
    a = await _note(services, "A note", "about the b note topic")
    b = await _note(services, "B note", "b note topic details")
    await services.db.execute(
        """
        INSERT INTO note_edges (source_note_id, target_note_id, relation_type, status, source)
        VALUES ($1, $2, 'related_concept', 'rejected', 'human')
        """,
        a,
        b,
    )
    await WikiService(services).build_links(redo=True)
    assert (
        await services.db.fetchval(
            "SELECT status FROM note_edges WHERE source_note_id = $1 AND target_note_id = $2",
            a,
            b,
        )
        == "rejected"
    )


async def test_mocs_are_assigned_rendered_and_round_trip_cleanly(services) -> None:
    from personal_context_mcp.worker_api import _render_job

    fake = ScriptedJev({}, moc=("mcp", 0.95))
    _use(services, fake)
    wiki = WikiService(services)
    await wiki.add_moc("MCP", description="Model Context Protocol.")
    pkce = await _note(services, "PKCE", "Proof Key for Code Exchange.")
    oauth = await _note(services, "MCP OAuth", "Clients use PKCE during the flow.")

    await wiki.build_links()
    report = await wiki.assign_mocs()
    assert report.memberships_accepted == 2

    rendered = await wiki.render()
    assert rendered.notes_queued == 2 and rendered.mocs_queued == 1
    # Nothing changed since: nothing to queue.
    again = await wiki.render()
    assert again.notes_queued == 0 and again.mocs_queued == 0

    jobs = {
        row["job_type"]: dict(row)
        for row in await services.db.fetch(
            "SELECT * FROM sync_jobs WHERE status = 'approved' AND object_id = ANY($1::uuid[])"
            " OR job_type = 'write_moc'",
            [oauth],
        )
    }
    moc = await _render_job(services, jobs["write_moc"])
    assert moc is not None
    assert "## Fundamentals" in moc["content"]
    assert "[[mcp-oauth|MCP OAuth]]" in moc["content"]
    assert "[[pkce|PKCE]]" in moc["content"]

    note = await _render_job(services, jobs["write_note"])
    assert note is not None
    content = note["content"]
    assert GENERATED_START in content
    assert "[[pkce|PKCE]]" in content and "[[mcp|MCP]]" in content

    # A human edits the body in Obsidian; only the body comes back.
    edited = content.replace("Clients use PKCE", "Every client uses PKCE")
    vault_path = jobs["write_note"]["vault_path"]
    result = await services.notes.apply_vault_edit(
        vault_path=vault_path, content=edited, file_sha256="abc"
    )
    assert result["changed"] is True
    stored = await services.db.fetchrow(
        "SELECT content, vault_synced_sha256 FROM notes WHERE id = $1", oauth
    )
    assert stored["content"] == "Every client uses PKCE during the flow."
    assert stored["vault_synced_sha256"] == "abc"
    assert extract_note_body(edited, "MCP OAuth") == stored["content"]
    del pkce


async def test_seed_mocs_from_extracted_topics(services) -> None:
    for i in range(3):
        await services.db.execute(
            """
            INSERT INTO conversations (provider, provider_conversation_id, imported, metadata)
            VALUES ('claude', $1, true,
                    jsonb_build_object('extraction', jsonb_build_object('topics', $2::jsonb)))
            """,
            f"c{i}",
            ["MCP OAuth", "pgvector"] if i < 2 else ["MCP OAuth"],
        )
    created = await WikiService(services).seed_mocs(min_mentions=3)
    assert created == ["MCP OAuth"]


async def test_doctor_reports_orphans(services) -> None:
    _use(services, ScriptedJev({}))
    await _note(services, "Lonely note", "Nothing links here.")
    health = await WikiService(services).doctor()
    assert health["orphans"] == 1
    assert health["orphan_sample"] == ["Lonely note"]
