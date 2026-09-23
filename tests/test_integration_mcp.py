"""Integration: the MCP surface itself, over real HTTP, with real auth.

These are the tests that prove the wiring works end to end: a client
authenticates, lists tools, calls them, and is refused where it should be.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db

pytestmark = requires_db


@pytest.fixture
async def mcp_client(services, live_server):
    """A real MCP client over a real socket, with real auth."""
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as client:
        yield client


async def test_client_can_list_the_full_tool_surface(mcp_client) -> None:
    tools = {t.name for t in await mcp_client.list_tools()}

    # Spot-check one tool per domain rather than pinning the exact count,
    # which would fail for the wrong reason every time a tool is added.
    assert {
        "context_bootstrap",
        "context_search",
        "memory_save",
        "memory_search",
        "session_checkpoint",
        "project_list",
        "list_search",
        "list_add_item",
        "note_write",
        "artifact_save_bundle",
        "artifact_register_reference",
        "review_list",
        "sync_status",
    } <= tools


async def test_tool_descriptions_tell_the_model_when_not_to_call(mcp_client) -> None:
    tools = {t.name: t for t in await mcp_client.list_tools()}
    bootstrap = tools["context_bootstrap"].description or ""
    assert "trivial self-contained" in bootstrap


async def test_artifact_tools_warn_against_claiming_capture(mcp_client) -> None:
    tools = {t.name: t for t in await mcp_client.list_tools()}
    reference = tools["artifact_register_reference"].description or ""
    assert "WITHOUT claiming" in reference
    assert "not captured" in reference.lower()


async def test_memory_round_trip_through_mcp(mcp_client) -> None:
    saved = await mcp_client.call_tool(
        "memory_save",
        {
            "content": "Supabase is canonical structured state.",
            "subject": "architecture",
            "provenance": "user_statement",
        },
    )
    assert saved.data.outcome == "created"

    found = await mcp_client.call_tool("memory_search", {"query": "canonical structured state"})
    assert any("Supabase is canonical" in m.content for m in found.data)


async def test_checkpoint_then_bootstrap_through_mcp(mcp_client) -> None:
    """The full loop a provider actually performs."""
    await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "mcp-e2e-1",
            "title": "Memory architecture",
            "summary": "Settled the ownership model for structured state.",
            "decisions": [
                {
                    "statement": "Supabase is canonical structured state and Obsidian is curated knowledge.",
                    "project": "Personal AI",
                }
            ],
            "tasks": [{"text": "Test the Gemini connector on iPhone"}],
        },
    )

    package = await mcp_client.call_tool(
        "context_bootstrap",
        {"user_message": "What did I decide about my shared AI memory architecture?"},
    )
    joined = " ".join(item.content for item in package.data.items)
    assert "Supabase is canonical" in joined
    assert package.data.estimated_tokens <= package.data.token_budget


async def test_lists_round_trip_through_mcp(mcp_client) -> None:
    """Acceptance test 2: add from one client, read it back."""
    await mcp_client.call_tool(
        "list_create", {"name": "Personal AI tasks", "list_type": "task"}
    )
    await mcp_client.call_tool(
        "list_add_item",
        {"list": "personal-ai-tasks", "text": "Test Gemini connector on iPhone"},
    )

    result = await mcp_client.call_tool("list_get", {"list": "personal-ai-tasks"})
    assert [i["text"] for i in result.structured_content["items"]] == [
        "Test Gemini connector on iPhone"
    ]


async def test_artifact_bundle_through_mcp_reports_capture_honestly(mcp_client) -> None:
    result = await mcp_client.call_tool(
        "artifact_save_bundle",
        {
            "title": "Personal AI Dashboard",
            "files": {
                "index.html": "<!doctype html><h1>Dashboard</h1>",
                "styles.css": "body{margin:0}",
                "src/app.js": "console.log(1)",
            },
        },
    )
    assert result.structured_content["capture_status"] == "captured"
    artifact = result.structured_content["artifact"]
    assert artifact["file_count"] == 3
    assert artifact["artifact_type"] == "web_project"
    assert {f["relative_path"] for f in artifact["files"]} == {
        "index.html", "styles.css", "src/app.js"
    }


async def test_register_reference_through_mcp_says_it_is_not_captured(mcp_client) -> None:
    result = await mcp_client.call_tool(
        "artifact_register_reference",
        {"title": "whiteboard.jpg", "artifact_type": "image"},
    )
    payload = result.structured_content
    assert payload["capture_status"] == "pending_import"
    assert "NOT captured" in payload["tell_the_user"]
    assert payload["artifact"]["storage_status"] == "pending_import"


async def test_prompts_are_exposed_for_clients_without_skills(mcp_client) -> None:
    names = {p.name for p in await mcp_client.list_prompts()}
    assert {"recall_context", "checkpoint_session", "capture_artifacts", "manage_lists"} <= names


async def test_status_resource_reports_counts_not_content(mcp_client) -> None:
    import json

    await mcp_client.call_tool("memory_save", {"content": "A private thought."})
    contents = await mcp_client.read_resource("personal-context-mcp://status")
    payload = json.loads(contents[0].text)

    assert payload["memories"] == 1
    # Counts only. No snippets of anything.
    assert "A private thought" not in contents[0].text


async def test_skill_guides_are_served_as_resources(mcp_client) -> None:
    contents = await mcp_client.read_resource("personal-context-mcp://guide/recall-context")
    assert "Recall context" in contents[0].text


async def test_guide_resource_refuses_path_traversal(mcp_client) -> None:
    """A traversal URI must not read a file outside the skills directory.

    Either outcome is acceptable and both are safe: the URI fails to match the
    template at all (the server never routes it), or it routes and the handler
    refuses. What must never happen is file contents coming back.
    """
    from mcp.shared.exceptions import MCPError

    try:
        contents = await mcp_client.read_resource(
            "personal-context-mcp://guide/..%2F..%2F..%2Fetc%2Fpasswd"
        )
    except MCPError:
        return
    assert "root:" not in contents[0].text
    assert "No guide named" in contents[0].text


# --------------------------------------------------------- scope enforcement


@pytest.fixture
async def read_only_client(services, live_server):
    """A client whose operator policy grants read scopes only."""
    services.settings.client_scope_policy = {"*": ["read"]}
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as client:
        yield client


async def test_read_only_client_sees_only_read_tools(read_only_client) -> None:
    names = {t.name for t in await read_only_client.list_tools()}

    assert {"context_bootstrap", "memory_search", "list_search"} <= names
    # Write tools are not merely refused on call - they are not advertised.
    assert not ({
        "memory_save", "session_checkpoint", "artifact_save_bundle", "note_write"
    } & names)


async def test_read_only_client_is_refused_a_write_tool(read_only_client) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await read_only_client.call_tool(
            "memory_save", {"content": "should never be written"}
        )
