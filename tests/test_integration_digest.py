"""The digest for assistants that cannot connect, and its Google Doc sync."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from conftest import requires_db
from personal_context_mcp.digest import build_digest
from personal_context_mcp.gemini_sync import DriveSyncError, push
from personal_context_mcp.util import sha256_text

pytestmark = requires_db


async def test_digest_contains_context_and_leaves_out_sensitive(services) -> None:
    await services.memories.save(
        content="Prefers answers without em dashes.",
        kind="preference",
        subject="writing",
        provenance="user_statement",
    )
    await services.memories.save(
        content="Takes medication X daily.",
        kind="fact",
        subject="health",
        provenance="user_statement",
        sensitivity="highly_sensitive",
    )
    project_id, _ = await services.projects.create(
        name="Personal Context", description="Shared memory for four AI apps."
    )
    await services.db.execute(
        "INSERT INTO decisions (statement, statement_sha256, project_id, provenance) "
        "VALUES ($1, $2, $3, 'user_statement')",
        "Use Vercel for hosting.",
        sha256_text("Use Vercel for hosting."),
        project_id,
    )
    list_id, _ = await services.lists.create(name="Todo", list_type="task")
    await services.lists.add_item(list_id=list_id, text="Rotate the Google client secret")

    text = await build_digest(services.db, services.settings, owner="Dan")

    assert "Dan's Personal Context" in text
    assert "Prefers answers without em dashes. (you said)" in text
    assert "### Personal Context (active)" in text
    assert "Decided: Use Vercel for hosting. (you said)" in text
    assert "- [ ] Rotate the Google client secret" in text
    assert "medication" not in text  # highly_sensitive never leaves


def _drive(responses: dict[tuple[str, str], list[httpx.Response]], calls: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        key = (request.method, request.url.path)
        for (method, prefix), queue in responses.items():
            if method == request.method and request.url.path.startswith(prefix) and queue:
                return queue.pop(0)
        raise AssertionError(f"unexpected {key}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settings(services):
    return services.settings.model_copy(
        update={
            "google_drive_client_id": "cid",
            "google_drive_client_secret": SecretStr("secret"),
            "google_drive_refresh_token": SecretStr("refresh"),
            "gemini_digest_doc_id": "",
        }
    )


async def test_push_creates_then_updates_the_same_doc(services, tmp_path: Path) -> None:
    state = tmp_path / "gemini-sync.json"
    token = httpx.Response(200, json={"access_token": "at"})
    calls: list[httpx.Request] = []
    client = _drive(
        {
            ("POST", "/token"): [token, token],
            ("POST", "/upload/drive/v3/files"): [httpx.Response(200, json={"id": "doc1"})],
            ("PATCH", "/upload/drive/v3/files/doc1"): [httpx.Response(200, json={"id": "doc1"})],
        },
        calls,
    )
    settings = _settings(services)

    assert await push(settings, "# one", client=client, state_file=state) == ("doc1", True)
    assert json.loads(state.read_text()) == {"doc_id": "doc1"}
    create = next(c for c in calls if c.method == "POST" and "upload" in c.url.path)
    assert create.headers["authorization"] == "Bearer at"
    assert b"application/vnd.google-apps.document" in create.content

    assert await push(settings, "# two", client=client, state_file=state) == ("doc1", False)
    update = calls[-1]
    assert update.method == "PATCH" and update.content == b"# two"


async def test_push_recreates_a_deleted_doc(services, tmp_path: Path) -> None:
    state = tmp_path / "gemini-sync.json"
    state.write_text(json.dumps({"doc_id": "gone"}))
    calls: list[httpx.Request] = []
    client = _drive(
        {
            ("POST", "/token"): [httpx.Response(200, json={"access_token": "at"})],
            ("PATCH", "/upload/drive/v3/files/gone"): [httpx.Response(404)],
            ("POST", "/upload/drive/v3/files"): [httpx.Response(200, json={"id": "new"})],
        },
        calls,
    )
    assert await push(_settings(services), "# x", client=client, state_file=state) == ("new", True)
    assert json.loads(state.read_text()) == {"doc_id": "new"}


async def test_push_explains_a_missing_authorization(services, tmp_path: Path) -> None:
    settings = _settings(services).model_copy(update={"google_drive_refresh_token": SecretStr("")})
    with pytest.raises(DriveSyncError, match="authorize"):
        await push(settings, "# x", client=httpx.AsyncClient(), state_file=tmp_path / "s.json")


async def test_recent_means_when_it_happened_not_when_imported(services) -> None:
    """An import refreshes summary rows; a two-year-old chat is not recent."""
    from datetime import UTC, datetime, timedelta

    old_id, _ = await services.sessions.ensure_conversation(
        provider="claude",
        provider_conversation_id="old",
        title="Old chat",
        imported=True,
        started_at=datetime.now(UTC) - timedelta(days=700),
    )
    await services.sessions.add_messages(
        old_id,
        [
            {
                "role": "user",
                "content": "hi",
                "provenance": "user_statement",
                "created_at": datetime.now(UTC) - timedelta(days=700),
            }
        ],
        provider="claude",
    )
    async with services.db.transaction() as conn:
        await services.sessions._upsert_summary(
            conn,
            conversation_id=old_id,
            provider="claude",
            project_id=None,
            title="Old chat",
            summary="Long ago.",
            open_questions=[],
            important_context=[],
            sensitivity="normal",
            needs_review=False,
        )

    text = await build_digest(services.db, services.settings, recent_days=14)
    assert "Old chat" not in text
