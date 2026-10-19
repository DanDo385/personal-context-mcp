"""Integration: memory, dedup, projects, lists, retrieval, checkpoints.

Requires PC_TEST_DATABASE_URL pointing at a throwaway Postgres with pgvector.
"""

from __future__ import annotations

from conftest import requires_db
from personal_context_mcp.models import (
    CheckpointDecision,
    CheckpointFact,
    CheckpointListUpdate,
    CheckpointProjectUpdate,
    CheckpointTask,
)

pytestmark = requires_db


# ----------------------------------------------------------------- memory


async def test_memory_save_creates_then_deduplicates(services) -> None:
    first = await services.memories.save(
        content="Supabase is canonical structured state.", subject="architecture"
    )
    assert first.outcome == "created"

    again = await services.memories.save(
        content="Supabase is canonical structured state.", subject="architecture"
    )
    assert again.outcome == "duplicate"
    assert again.id == first.id


async def test_near_identical_memory_updates_in_place_and_keeps_history(services) -> None:
    first = await services.memories.save(
        content="Obsidian is the curated knowledge layer.", subject="architecture"
    )
    second = await services.memories.save(
        content="Obsidian is the curated knowledge layer!!", subject="architecture"
    )
    assert second.outcome == "updated"
    assert second.id == first.id

    history = await services.memories.history(first.id)
    assert len(history) == 2
    assert history[0]["version"] == 2


async def test_different_facts_about_one_subject_stay_separate(services) -> None:
    a = await services.memories.save(
        content="The vault lives at ~/Obsidian/Vault.", subject="architecture"
    )
    b = await services.memories.save(
        content="Artifacts are stored in Supabase Storage, not Postgres.",
        subject="architecture",
    )
    assert a.id != b.id
    assert b.outcome == "created"


async def test_memory_search_finds_by_text(services) -> None:
    await services.memories.save(
        content="Morgan Stanley is a networking follow-up.", subject="networking"
    )
    results = await services.memories.search("Morgan Stanley")
    assert any("Morgan Stanley" in r.content for r in results)


async def test_provenance_is_preserved_verbatim(services) -> None:
    saved = await services.memories.save(
        content="I prefer short summaries.", provenance="user_statement"
    )
    record = await services.memories.get(saved.id)
    assert record.provenance.kind == "user_statement"

    inferred = await services.memories.save(
        content="The user seems to dislike long preambles.",
        provenance="assistant_generated",
    )
    assert (await services.memories.get(inferred.id)).provenance.kind == "assistant_generated"


async def test_retracted_memory_leaves_search(services) -> None:
    saved = await services.memories.save(content="A temporary belief.", subject="temp")
    await services.memories.retract(saved.id, reason="was wrong")
    assert not any(r.id == saved.id for r in await services.memories.search("temporary belief"))


# --------------------------------------------------------------- projects


async def test_project_slug_collapses_name_variants(services) -> None:
    first, created = await services.projects.create(name="Personal AI")
    assert created
    again, created_again = await services.projects.create(name="personal ai")
    assert not created_again
    assert again == first

    assert await services.projects.resolve("Personal AI") == first
    assert await services.projects.resolve("personal-ai") == first
    assert await services.projects.resolve(str(first)) == first


async def test_unknown_project_resolves_to_none_not_a_new_row(services) -> None:
    assert await services.projects.resolve("no-such-project") is None
    assert await services.projects.list_all() == []


async def test_project_events_deduplicate(services) -> None:
    project_id, _ = await services.projects.create(name="Alpha")
    first = await services.projects.add_event(project_id, summary="Kicked off")
    second = await services.projects.add_event(project_id, summary="Kicked off")
    assert first is not None
    assert second is None


# ------------------------------------------------------------------ lists


async def test_list_crud_and_item_deduplication(services) -> None:
    list_id, created = await services.lists.create(name="Personal AI tasks", list_type="task")
    assert created

    first_id, added = await services.lists.add_item(
        list_id=list_id, text="Test Gemini connector on iPhone"
    )
    assert added
    _, added_again = await services.lists.add_item(
        list_id=list_id, text="test gemini connector on iphone  "
    )
    assert not added_again

    items = await services.lists.items("personal-ai-tasks")
    assert len(items) == 1
    assert items[0].id == first_id


async def test_item_resolution_prefers_id_over_text(services) -> None:
    list_id, _ = await services.lists.create(name="Reading")
    item_id, _ = await services.lists.add_item(list_id=list_id, text="Attention Is All You Need")

    assert await services.lists.resolve_item(list_id, str(item_id)) == item_id
    assert await services.lists.resolve_item(list_id, "attention is all you need") == item_id
    assert await services.lists.resolve_item(list_id, "something else") is None


async def test_completing_an_item_sets_a_timestamp(services) -> None:
    list_id, _ = await services.lists.create(name="Tasks", list_type="task")
    item_id, _ = await services.lists.add_item(list_id=list_id, text="Ship the MCP server")
    await services.lists.complete_item(item_id)

    done = await services.lists.items("tasks", status="done")
    assert len(done) == 1
    assert done[0].completed_at is not None


async def test_moving_an_item_between_lists(services) -> None:
    someday, _ = await services.lists.create(name="Someday")
    active, _ = await services.lists.create(name="Active")
    item_id, _ = await services.lists.add_item(list_id=someday, text="Browser capture extension")

    await services.lists.move_item(item_id, target_list_id=active)
    assert [i.text for i in await services.lists.items("active")] == ["Browser capture extension"]
    assert await services.lists.items("someday") == []


async def test_move_onto_a_duplicate_collapses_instead_of_duplicating(services) -> None:
    a, _ = await services.lists.create(name="A")
    b, _ = await services.lists.create(name="B")
    item_id, _ = await services.lists.add_item(list_id=a, text="same thing")
    await services.lists.add_item(list_id=b, text="same thing")

    await services.lists.move_item(item_id, target_list_id=b)
    assert len(await services.lists.items("b")) == 1


# ------------------------------------------------------------- checkpoint


async def _checkpoint(services, **kwargs):
    from personal_context_mcp.auth.principal import Principal

    defaults = {
        "provider": "claude",
        "principal": Principal(subject="tester@example.com", provider="claude"),
        "provider_conversation_id": "conv-1",
        "title": "Shared memory architecture",
        "summary": "We settled the ownership model for structured state and knowledge.",
    }
    defaults.update(kwargs)
    return await services.sessions.checkpoint(**defaults)


async def test_checkpoint_persists_everything_in_one_call(services) -> None:
    result = await _checkpoint(
        services,
        decisions=[
            CheckpointDecision(
                statement="Supabase is canonical structured state and Obsidian is curated knowledge.",
                rationale="Postgres gives us integrity; the vault gives us a human interface.",
                project="Personal AI",
            )
        ],
        facts=[CheckpointFact(content="The MCP server is deployed to Cloud Run.")],
        tasks=[CheckpointTask(text="Test the Gemini connector on iPhone")],
        project_updates=[
            CheckpointProjectUpdate(project="Personal AI", status="active", event="Design settled")
        ],
        open_questions=["Does Grok support static OAuth client ids yet?"],
    )

    assert result.created.get("decisions") == 1
    assert result.created.get("memories", 0) >= 1
    assert result.created.get("tasks") == 1
    assert result.created.get("projects") == 1
    assert result.session_summary_id is not None


async def test_checkpoint_is_idempotent(services) -> None:
    payload = {
        "decisions": [CheckpointDecision(statement="Use pgvector for embeddings.")],
        "facts": [CheckpointFact(content="Embeddings are 1536-dimensional.")],
        "tasks": [CheckpointTask(text="Write the README")],
    }
    first = await _checkpoint(services, **payload)
    second = await _checkpoint(services, **payload)

    assert first.conversation_id == second.conversation_id
    assert second.created.get("decisions", 0) == 0
    assert second.skipped.get("decisions") == 1
    assert second.skipped.get("tasks") == 1

    count = await services.db.fetchval("SELECT count(*) FROM decisions")
    assert count == 1


async def test_checkpoint_without_a_conversation_id_still_collapses(services) -> None:
    """No provider conversation id is the common case; it must still dedupe."""
    a = await _checkpoint(services, provider_conversation_id=None, title="Same title")
    b = await _checkpoint(services, provider_conversation_id=None, title="Same title")
    assert a.conversation_id == b.conversation_id


async def test_checkpoint_reports_a_missing_list_item_instead_of_guessing(services) -> None:
    await services.lists.create(name="Reading")
    result = await _checkpoint(
        services,
        list_updates=[CheckpointListUpdate(list="Reading", complete=["a book we never added"])],
    )
    assert result.skipped.get("list_items") == 1
    assert any("nothing was completed" in note for note in result.notes)


async def test_checkpoint_warns_when_it_invents_a_list(services) -> None:
    result = await _checkpoint(
        services, list_updates=[CheckpointListUpdate(list="Brand New List", add=["first item"])]
    )
    assert result.created.get("lists") == 1
    assert any("Created list" in note for note in result.notes)


async def test_checkpoint_refuses_to_attach_an_artifact_without_an_id(services) -> None:
    from personal_context_mcp.models import CheckpointArtifactRef

    result = await _checkpoint(services, artifacts=[CheckpointArtifactRef(title="A chart I made")])
    assert result.skipped.get("artifacts") == 1
    assert any("capture it with artifact_save" in note for note in result.notes)


# ------------------------------------------------------------- retrieval


async def test_cross_provider_recall(services) -> None:
    """Acceptance test 1: Claude checkpoints, Gemini retrieves."""
    await _checkpoint(
        services,
        provider="claude",
        project_updates=[CheckpointProjectUpdate(project="Personal AI")],
        decisions=[
            CheckpointDecision(
                statement="Supabase is canonical structured state and Obsidian is curated knowledge.",
                project="Personal AI",
            )
        ],
    )

    # The question shares no words with the decision, so it is found through
    # the project's handoff, not the text search.
    package = await services.retrieval.bootstrap(
        "What did I decide about my shared AI memory architecture?", project="Personal AI"
    )
    joined = " ".join(item.content for item in package.items)
    assert "Supabase is canonical" in joined

    decision_items = [i for i in package.items if i.type in ("decision", "memory")]
    assert decision_items
    # Provenance survives the hop between providers.
    assert any(i.provenance.provider == "claude" for i in decision_items)


async def test_bootstrap_respects_its_token_budget(services) -> None:
    for i in range(40):
        await services.memories.save(
            content=f"Fact number {i} about the personal AI project, " + "padding " * 60,
            subject=f"subject-{i}",
        )
    package = await services.retrieval.bootstrap("personal AI project", token_budget=600)
    assert package.estimated_tokens <= 600
    assert package.items


async def test_bootstrap_says_so_when_nothing_matches(services) -> None:
    package = await services.retrieval.bootstrap("an entirely unrelated question about lemurs")
    assert not package.items
    assert any("do not assume prior context" in note for note in package.notes)


async def test_search_prefers_structured_layers_over_raw_messages(services) -> None:
    conversation_id, _ = await services.sessions.ensure_conversation(
        provider="chatgpt", provider_conversation_id="raw-1", title="Raw chat"
    )
    await services.sessions.add_messages(
        conversation_id,
        [{"role": "user", "content": "Tell me about quantisation in transformers."}],
        provider="chatgpt",
    )
    await services.memories.save(content="Quantisation trades precision for memory.")

    without = await services.retrieval.search("quantisation", include_messages=False)
    assert all(i.type != "message" for i in without)

    with_messages = await services.retrieval.search("quantisation", include_messages=True)
    assert any(i.type == "message" for i in with_messages)
