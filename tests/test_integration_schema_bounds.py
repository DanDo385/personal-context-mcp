"""Integration: every numeric bound a tool advertises is the bound it enforces.

token_budget=6000 once passed a tool's schema and then failed at runtime
against a hidden maximum of 4000. Here every tool's advertised numeric
parameters are called at their minimum and maximum through real MCP and must
succeed, and one step past the maximum must be refused up front.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from conftest import requires_db

pytestmark = requires_db

#: The smallest valid call for each tool that has a bounded parameter.
REQUIRED: dict[str, dict[str, Any]] = {
    "context_bootstrap": {},
    "context_search": {"query": "anything"},
    "context_recent": {},
    "context_for_topic": {"topic": "anything"},
    "memory_save": {"content": "Bounds are honest."},
    "memory_search": {"query": "anything"},
    "session_recent": {},
    "project_list": {},
    "project_search": {"query": "anything"},
    "note_search": {"query": "anything"},
    "list_search": {},
    "list_get": {"list": "bounds"},
    "list_add_item": {"list": "bounds", "text": "an item"},
    "list_recent": {},
    "list_find_item": {"query": "item"},
    "artifact_begin_upload": {"title": "t", "filename": "t.txt", "mime_type": "text/plain"},
    "artifact_search": {},
    "review_list": {},
}

#: Numeric parameters allowed to have no declared maximum, and why.
UNBOUNDED_OK = {
    ("artifact_begin_upload", "byte_size"): "capped by the storage limit, reported as an error",
}


@pytest.fixture
def unthrottled(settings):
    settings.rate_limit_per_minute = 1_000_000
    settings.rate_limit_burst = 1_000_000
    return settings


@pytest.fixture
async def client(unthrottled, services, live_server):
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as c:
        await c.call_tool("list_create", {"name": "bounds"})
        yield c


async def test_every_advertised_bound_is_the_enforced_bound(client) -> None:
    checked: list[str] = []
    for tool in await client.list_tools():
        properties = (tool.inputSchema or {}).get("properties", {})
        for name, spec in properties.items():
            if spec.get("type") not in ("integer", "number"):
                continue
            if "maximum" not in spec and (tool.name, name) not in UNBOUNDED_OK:
                pytest.fail(f"{tool.name}.{name} has no declared maximum")
            assert tool.name in REQUIRED, f"add a minimal call for {tool.name} to REQUIRED"
            base = dict(REQUIRED[tool.name])
            if tool.name == "artifact_begin_upload":
                base["byte_size"] = 1
            for bound in ("minimum", "maximum"):
                if bound not in spec:
                    continue
                result = await client.call_tool(
                    tool.name, {**base, name: spec[bound]}, raise_on_error=False
                )
                assert not result.is_error, (tool.name, name, spec[bound], result.content)
                checked.append(f"{tool.name}.{name}={spec[bound]}")
            if "maximum" in spec:
                step = 1 if spec["type"] == "integer" else 0.5
                with pytest.raises(ToolError) as refused:
                    await client.call_tool(tool.name, {**base, name: spec["maximum"] + step})
                assert name in str(refused.value)
    assert len(checked) >= 30, checked
