"""Integration: what a new chat gets from context_bootstrap, through real MCP.

One test per problem observed on 2026-10-17, written against tool behavior
(arguments in, package out) so the same cases can be pointed at a successor
tool. The synthetic fixture is in tests/eval/fixture.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db
from eval.fixture import Seeded, seed

pytestmark = requires_db


@pytest.fixture
def unthrottled(settings):
    settings.rate_limit_per_minute = 1_000_000
    settings.rate_limit_burst = 1_000_000
    return settings


@pytest.fixture
async def world(unthrottled, db, live_server):
    seeded = await seed(db)
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as client:
        yield client, seeded


async def _bootstrap(client: Any, **args: Any) -> dict[str, Any]:
    result = await client.call_tool("context_bootstrap", args)
    return result.structured_content or {}


def _keys(package: dict[str, Any], seeded: Seeded) -> list[str | None]:
    return [seeded.key_for(i["id"]) for i in package.get("items", [])]


def _layer(package: dict[str, Any], name: str) -> dict[str, Any]:
    return next(layer for layer in package["layers"] if layer["name"] == name)


async def test_problem_1_handoff_comes_first_with_open_questions(world) -> None:
    client, seeded = world
    package = await _bootstrap(
        client, project="harbor-notes", query="open source", token_budget=1200
    )
    keys = _keys(package, seeded)

    # Newest sessions, open tasks and active decisions, before anything searched.
    handoff = [
        k for k, i in zip(keys, package["items"], strict=True) if i.get("layer") == "handoff"
    ]
    assert {"s_hn_untagged", "s_hn_sync", "s_hn_opensource"} <= set(handoff)
    assert {"t_hn_guide", "t_hn_tomb", "d_hn_render"} <= set(handoff)
    assert keys.index("s_hn_untagged") < keys.index("s_hn_sync")  # newest first
    sync = package["items"][keys.index("s_hn_sync")]
    assert sync["open_questions"] == ["Should deleted paragraphs leave a tombstone?"]
    assert sync["provenance"]["provider"] == "claude"
    assert sync["effective_at"]
    assert "t_hn_host" not in keys  # done tasks are not open tasks

    # Which layers ran, which did not, and why.
    assert _layer(package, "handoff")["status"] == "ran"
    assert _layer(package, "relevant")["status"] == "ran"
    raw = _layer(package, "raw_messages")
    assert raw["status"] == "skipped" and raw["reason"]


async def test_problem_2_newer_statement_is_flagged_not_hidden(world) -> None:
    client, seeded = world
    package = await _bootstrap(client, project="harbor-notes", query="open source v2")
    by_key = {seeded.key_for(i["id"]): i for i in package["items"]}

    older = by_key["d_hn_alone"]
    assert seeded.ids["s_hn_opensource"] in {_uuid(x) for x in older["conflicts_with"]}
    conflict = next(
        c for c in package["conflicts"] if _uuid(c["ids"][0]) == seeded.ids["d_hn_alone"]
    )
    assert conflict["kind"] == "possible_conflict"
    assert conflict["newer_provenance"] == "assistant_generated"
    assert "ask the user" in conflict["advice"]

    # An explicit supersede link is reported as such, with the replacement.
    found = await client.call_tool("context_search", {"query": "store data in SQLite"})
    items = {seeded.key_for(i["id"]): i for i in found.structured_content["result"]}
    assert items["d_hn_sqlite"]["status"] == "superseded"
    assert _uuid(items["d_hn_sqlite"]["superseded_by"]) == seeded.ids["d_hn_postgres"]


async def test_problem_3_small_budgets_still_get_the_handoff(world) -> None:
    client, seeded = world
    for budget in (200, 400, 600):
        package = await _bootstrap(client, project="harbor-notes", token_budget=budget)
        keys = _keys(package, seeded)
        assert keys, budget
        assert keys[0] == "s_hn_untagged", budget  # the newest session comes first
        assert package["estimated_tokens"] <= budget


async def test_problem_4_untagged_sessions_reach_the_handoff(world) -> None:
    client, seeded = world
    package = await _bootstrap(client, project="harbor-notes")
    item = next(i for i in package["items"] if seeded.key_for(i["id"]) == "s_hn_untagged")
    assert "no project" in item["reason"]

    # A checkpoint that names its project is saved under it.
    await client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "tagged-1",
            "title": "Tombstone design",
            "summary": "Settled that deleted paragraphs keep a tombstone for thirty days.",
            "open_questions": ["Should tombstones sync to phones?"],
            "project": "harbr notes",  # a misspelling still finds the project
        },
    )
    package = await _bootstrap(client, project="harbor-notes")
    first = package["items"][0]
    assert first["title"] == "Tombstone design"
    assert first["project"] == "harbor-notes"
    assert first["open_questions"] == ["Should tombstones sync to phones?"]


async def test_checkpoint_with_unknown_project_says_so(world) -> None:
    client, _ = world
    result = await client.call_tool(
        "session_checkpoint",
        {"provider": "claude", "summary": "Something happened.", "project": "no such project"},
    )
    assert any("No project matches" in n for n in result.structured_content["notes"])


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("what is next for harbor notes", "harbor-notes"),
        ("tests failing in ~/Code/worktrees/harbor-notes/fix-search", "harbor-notes"),
        ("github.com/someone/harbor-notes issues", "harbor-notes"),
        ("harbr notes status", "harbor-notes"),
        ("lh api rate limit", "lighthouse-api"),
    ],
)
async def test_problem_5_project_is_inferred_and_explained(world, text: str, slug: str) -> None:
    client, _ = world
    package = await _bootstrap(client, query=text)
    assert package["project"]["slug"] == slug
    assert package["project"]["source"] == "inferred"
    assert package["project"]["reason"]


async def test_ambiguous_project_gets_both_short_handoffs(world) -> None:
    client, seeded = world
    package = await _bootstrap(client, query="atlas status")
    assert package["project"]["source"] == "none"
    assert {c["slug"] for c in package["project"]["candidates"]} == {"atlas-ledger", "atlas-mobile"}
    assert {"s_al_tax", "s_am_push"} <= set(_keys(package, seeded))


async def test_old_callers_passing_user_message_still_work(world) -> None:
    client, seeded = world
    package = await _bootstrap(
        client,
        user_message=(
            "Hi! Quick one about the Harbor Notes app: where are we deploying the server "
            "these days, and is anything still open there? Thanks so much!"
        ),
    )
    assert package["project"]["slug"] == "harbor-notes"
    assert "d_hn_render" in _keys(package, seeded)
    assert "thanks" not in package["keywords"]
    assert any("deprecated" in n for n in package["notes"])


async def test_unrelated_question_returns_nothing_and_says_so(world) -> None:
    client, _ = world
    package = await _bootstrap(client, query="a good name for a cat")
    assert package.get("items", []) == []
    assert any("do not assume prior context" in n for n in package["notes"])


def _uuid(value: Any):
    from uuid import UUID

    return UUID(str(value))
