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
