"""Slim projections for tool results.

Host models pay for every byte of a tool result, and some hosts also echo the
structured payload as text. Default responses therefore keep only the fields a
caller needs to answer or to take the next action. Pass detail="full" for the
complete record.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from personal_context_mcp.models import ListItemRecord, ListRecord


def dump(value: Any) -> Any:
    """JSON-ready value with nulls and empty containers removed."""
    if isinstance(value, BaseModel):
        return dump(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, list):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            projected = dump(item)
            if projected is None or projected == "" or projected == [] or projected == {}:
                continue
            out[key] = projected
        return out
    return value


def list_summary(record: ListRecord) -> dict[str, Any]:
    out: dict[str, Any] = {
        "slug": record.slug,
        "name": record.name,
        "open": record.open_items,
    }
    if record.description:
        out["description"] = record.description
    if record.list_type != "general":
        out["type"] = record.list_type
    if record.project:
        out["project"] = record.project
    if record.status != "active":
        out["status"] = record.status
    return out


def list_item(item: ListItemRecord, *, detail: str = "compact") -> dict[str, Any]:
    if detail == "full":
        return dump(item)
    out: dict[str, Any] = {"id": str(item.id), "text": item.text}
    if item.status != "open":
        out["status"] = item.status
    if item.priority:
        out["priority"] = item.priority
    if item.due_date is not None:
        out["due_date"] = item.due_date.isoformat()
    if item.notes:
        out["notes"] = item.notes
    return out
