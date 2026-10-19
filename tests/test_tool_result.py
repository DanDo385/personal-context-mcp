"""list_get and context_bootstrap send one line of text and keep the object structured."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastmcp import FastMCP
from fastmcp.server.mixins.mcp_operations import _normalize_call_tool_result
from mcp.types import TextContent

from personal_context_mcp.mcp import tools_context, tools_lists
from personal_context_mcp.mcp.result import text_and_structured
from personal_context_mcp.models import (
    ContextItem,
    ContextPackage,
    ListItemRecord,
    ListRecord,
    Provenance_,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)
LIST_ID = UUID("00000000-0000-4000-8000-000000000010")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000011")
NOTE_ID = UUID("00000000-0000-4000-8000-000000000012")
ITEM_TEXT = "Buy oat milk from the shop on the corner"
ITEM_NOTES = "the long note that must stay out of the text block"


class _Lists:
    def __init__(self, record: ListRecord, items: list[ListItemRecord]) -> None:
        self.record = record
        self._items = items

    async def get(self, _reference: str) -> ListRecord:
        return self.record

    async def items(
        self, _reference: str, status: str | None = None, limit: int = 40
    ) -> list[ListItemRecord]:
        return self._items[:limit]


class _Retrieval:
    def __init__(self, package: ContextPackage) -> None:
        self.package = package

    async def bootstrap(
        self,
        _query: str | None = None,
        *,
        user_message: str | None = None,
        token_budget: int = 1200,
        project: str | None = None,
    ) -> ContextPackage:
        return self.package


class _Services:
    def __init__(self, lists: _Lists | None = None, retrieval: _Retrieval | None = None) -> None:
        self.lists = lists
        self.retrieval = retrieval


def _item() -> ListItemRecord:
    return ListItemRecord(
        id=ITEM_ID,
        list_id=LIST_ID,
        list_slug="groceries",
        text=ITEM_TEXT,
        status="open",
        priority=0,
        position=0,
        notes=ITEM_NOTES,
        created_at=NOW,
    )


def _record(open_items: int) -> ListRecord:
    return ListRecord(
        id=LIST_ID,
        slug="groceries",
        name="Groceries",
        list_type="shopping",
        status="active",
        open_items=open_items,
        total_items=open_items,
    )


def _package(*, truncated: bool, tokens: int, contents: list[str]) -> ContextPackage:
    return ContextPackage(
        query="what did I decide about the secret architecture",
        items=[
            ContextItem(
                type="decision",
                id=NOTE_ID,
                content=content,
                provenance=Provenance_(kind="user_statement"),
                estimated_tokens=tokens // len(contents),
            )
            for content in contents
        ],
        token_budget=1200,
        estimated_tokens=tokens,
        layers_used=["memory"],
        truncated=truncated,
    )


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    holder: dict[str, Any] = {}
    monkeypatch.setattr(tools_lists, "get_services", lambda: holder["services"])
    monkeypatch.setattr(tools_context, "get_services", lambda: holder["services"])
    server = FastMCP("token-cut-test")
    tools_lists.register(server)
    tools_context.register(server)
    server._holder = holder  # type: ignore[attr-defined]
    return server


async def _tool(mcp: FastMCP, name: str) -> Any:
    """The registered function, skipping the auth check that hides it from get_tool."""
    tool = await mcp.local_provider._get_tool(name)
    assert tool is not None
    return tool


def _wire(result: Any) -> tuple[str, dict[str, Any]]:
    raw = result.to_mcp_result()
    assert isinstance(raw, tuple)
    content, structured = raw
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    normalized = _normalize_call_tool_result(raw)
    assert len(normalized.content) == 1
    assert isinstance(normalized.content[0], TextContent)
    assert normalized.content[0].text == content[0].text
    assert normalized.structured_content == structured
    text = content[0].text
    assert "\n" not in text
    assert text != json.dumps(structured)
    assert json.dumps(structured) not in text
    return text, structured


async def test_list_get_text_is_one_line_and_items_stay_structured(mcp: FastMCP) -> None:
    mcp._holder["services"] = _Services(lists=_Lists(_record(5), [_item()]))  # type: ignore[attr-defined]
    tool = await _tool(mcp, "list_get")
    assert tool.output_schema == {"type": "object", "additionalProperties": True}

    text, structured = _wire(await tool.run({"list": "groceries"}))

    assert text == "Groceries: 1 of 5 open"
    assert ITEM_TEXT not in text
    assert ITEM_NOTES not in text
    assert structured["list"] == "groceries"
    assert structured["shown"] == 1
    assert structured["open"] == 5
    assert structured["truncated"] is True
    assert structured["items"] == [{"id": str(ITEM_ID), "text": ITEM_TEXT, "notes": ITEM_NOTES}]


async def test_list_get_line_when_the_open_items_all_fit(mcp: FastMCP) -> None:
    mcp._holder["services"] = _Services(lists=_Lists(_record(1), [_item()]))  # type: ignore[attr-defined]
    tool = await _tool(mcp, "list_get")

    text, structured = _wire(await tool.run({"list": "groceries"}))

    assert text == "Groceries: 1 shown, 1 open"
    assert structured["truncated"] is False
    assert "status" not in structured["items"][0]


async def test_context_bootstrap_text_omits_the_package(mcp: FastMCP) -> None:
    secret = "Supabase is canonical structured state"
    mcp._holder["services"] = _Services(  # type: ignore[attr-defined]
        retrieval=_Retrieval(_package(truncated=False, tokens=40, contents=[secret]))
    )
    tool = await _tool(mcp, "context_bootstrap")
    assert tool.output_schema == {"type": "object", "additionalProperties": True}

    text, structured = _wire(
        await tool.run({"user_message": "what did I decide about the secret architecture"})
    )

    assert text == "1 item, 40 of 1200 tokens"
    assert secret not in text
    assert "secret architecture" not in text
    assert structured["items"][0]["content"] == secret
    assert structured["estimated_tokens"] == 40
    assert structured["token_budget"] == 1200
    assert structured["truncated"] is False


async def test_context_bootstrap_line_marks_truncation(mcp: FastMCP) -> None:
    mcp._holder["services"] = _Services(  # type: ignore[attr-defined]
        retrieval=_Retrieval(
            _package(truncated=True, tokens=900, contents=["one body", "another body"])
        )
    )
    tool = await _tool(mcp, "context_bootstrap")

    text, structured = _wire(await tool.run({"user_message": "catch me up"}))

    assert text == "2 items, 900 of 1200 tokens, truncated"
    assert "one body" not in text
    assert structured["truncated"] is True
    assert len(structured["items"]) == 2


def test_text_and_structured_collapses_whitespace() -> None:
    result = text_and_structured("Groceries:\n1 of 5 open", {"shown": 1})
    text, structured = _wire(result)
    assert text == "Groceries: 1 of 5 open"
    assert structured == {"shown": 1}
