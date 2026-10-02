"""One line of text, with the object left in structured content.

FastMCP copies a plain dict into the text block and into structured content.
Returning a ToolResult keeps the text to a single line. The return annotation
is then ToolResult, which advertises no output schema, so callers pass
object_output_schema() and the structured object stays an object.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools.base import ToolResult

__all__ = ["ToolResult", "object_output_schema", "text_and_structured"]


def object_output_schema() -> dict[str, Any]:
    """The same object schema a tool annotated `-> dict` would advertise."""
    return {"type": "object", "additionalProperties": True}


def text_and_structured(text: str, payload: dict[str, Any]) -> ToolResult:
    """Collapse `text` to one line and attach `payload` as structured content."""
    line = " ".join(text.split())
    if not line:
        raise ValueError("text result must be one line")
    return ToolResult(content=line, structured_content=payload)
