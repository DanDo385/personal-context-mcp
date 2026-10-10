"""The probe's schema checks, without a server."""

from __future__ import annotations

from personal_context_mcp.probe import schema_problems


def test_a_clean_schema_has_no_problems() -> None:
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum results."},
            "project": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Project slug or name.",
            },
        },
        "required": ["limit"],
    }
    assert schema_problems("tool", schema) == []


def test_missing_descriptions_types_and_refs_are_reported() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"description": "untyped"},
            "c": {"$ref": "#/$defs/C", "description": "by reference"},
        },
        "required": ["a", "missing"],
        "$defs": {"C": {"type": "string"}},
    }
    problems = schema_problems("tool", schema)
    assert "tool.a: no description" in problems
    assert "tool.b: no explicit type" in problems
    assert "tool: required 'missing' is not a declared property" in problems
    assert any("$ref" in p for p in problems)


def test_a_non_object_root_is_reported() -> None:
    assert any("not 'object'" in p for p in schema_problems("tool", {"type": "string"}))


async def test_registered_tool_schemas_pass_the_probe() -> None:
    """The live tool list satisfies the same checker `probe` uses on tools/list."""
    from personal_context_mcp.config import Settings
    from personal_context_mcp.server import build_mcp

    settings = Settings(
        auth_mode="none",
        database_url="postgres://unused",
        classifier="rules",
        storage_backend="local",
        typesafe_api_key="unused",
        oauth_signing_key="unused",
        worker_api_key="unused",
    )
    tools = await build_mcp(settings).list_tools()
    problems: list[str] = []
    for tool in tools:
        schema = getattr(tool, "parameters", None)
        problems.extend(schema_problems(tool.name, schema))
    assert problems == []
