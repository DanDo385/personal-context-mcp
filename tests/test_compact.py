from datetime import date, datetime, timezone
from uuid import uuid4

from personal_context_mcp.mcp.compact import dump, list_item, list_summary
from personal_context_mcp.models import ListItemRecord, ListRecord


def test_list_summary_omits_empty_and_defaults() -> None:
    record = ListRecord(
        id=uuid4(),
        slug="todo",
        name="Todo",
        description=None,
        list_type="task",
        project=None,
        status="active",
        open_items=3,
        total_items=3,
    )
    assert list_summary(record) == {
        "slug": "todo",
        "name": "Todo",
        "open": 3,
        "type": "task",
    }


def test_list_item_compact_keeps_id_and_drops_nulls() -> None:
    item = ListItemRecord(
        id=uuid4(),
        list_id=uuid4(),
        list_slug="todo",
        text="Close TD accounts",
        status="open",
        priority=0,
        position=3,
        due_date=None,
        notes=None,
        created_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        completed_at=None,
    )
    projected = list_item(item)
    assert projected == {"id": str(item.id), "text": "Close TD accounts"}
    assert "list_id" not in projected
    assert "created_at" not in projected


def test_list_item_keeps_notes_and_due_date() -> None:
    item = ListItemRecord(
        id=uuid4(),
        list_id=uuid4(),
        list_slug="todo",
        text="File review",
        status="blocked",
        priority=2,
        position=1,
        due_date=date(2026, 10, 5),
        notes="From past chats",
        created_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        completed_at=None,
    )
    assert list_item(item) == {
        "id": str(item.id),
        "text": "File review",
        "status": "blocked",
        "priority": 2,
        "due_date": "2026-10-05",
        "notes": "From past chats",
    }


def test_dump_strips_nested_nulls() -> None:
    assert dump({"a": 1, "b": None, "c": [], "d": {"e": None, "f": "x"}}) == {
        "a": 1,
        "d": {"f": "x"},
    }
