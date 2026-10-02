"""Session index bodies: cap, skip, takeout-day wording, conditional wikilinks."""

from __future__ import annotations

import re
from datetime import date, timedelta

from personal_context_mcp.obsidian.paths import VaultPaths
from personal_context_mcp.sessions.index import (
    SessionIndexRow,
    index_date,
    render_session_index,
)

ROOTS = {
    "inbox": "00-Inbox",
    "project": "20-Projects",
    "resource": "60-Resources",
    "atlas": "70-Atlas",
}


def row(**overrides: object) -> SessionIndexRow:
    values: dict[str, object] = {
        "provider": "chatgpt",
        "provider_conversation_id": "c1",
        "title": "Hello",
        "occurred_on": date(2026, 1, 1),
        "summary": "First line.\nSecond line that must stay out.",
        "message_count": 2,
    }
    values.update(overrides)
    return SessionIndexRow(**values)  # type: ignore[arg-type]


def test_session_index_path_is_under_atlas() -> None:
    paths = VaultPaths(ROOTS)
    assert paths.session_index("ChatGPT") == "70-Atlas/chatgpt.md"
    assert paths.note(note_class="session_index", title="Gemini") == "70-Atlas/gemini.md"


def test_index_keeps_newest_200_and_counts_the_rest() -> None:
    rows = [
        row(
            provider_conversation_id=f"c{i}",
            title=f"Item {i:04d}",
            occurred_on=date(2024, 1, 1) + timedelta(days=i),
            summary="kept",
            message_count=1,
        )
        for i in range(205)
    ]
    body = render_session_index("chatgpt", rows)
    assert "Item 0204" in body
    assert "Item 0005" in body
    assert "Item 0004" not in body
    assert body.count("\n- ") == 200
    assert "5 older" in body


def test_zero_message_rows_are_skipped_and_not_counted_as_older() -> None:
    rows = [
        row(provider_conversation_id="a", message_count=0, title="Empty one"),
        row(provider_conversation_id="b", message_count=0, title="Empty two"),
        row(provider_conversation_id="c", message_count=0, title="Empty three"),
        row(provider_conversation_id="d", message_count=4, title="Kept", summary="Visible line"),
    ]
    body = render_session_index("chatgpt", rows)
    assert "Kept" in body
    assert "Visible line" in body
    assert "Empty one" not in body
    assert "Empty two" not in body
    assert "Empty three" not in body
    assert "older" not in body


def test_duplicate_provider_conversation_keeps_one_row() -> None:
    rows = [
        row(
            provider_conversation_id="same",
            title="Older title",
            occurred_on=date(2024, 1, 1),
        ),
        row(
            provider="chatgpt",
            provider_conversation_id="same",
            title="Newer title",
            occurred_on=date(2024, 6, 1),
            note_stem="newer-note",
        ),
    ]
    body = render_session_index("chatgpt", rows)
    assert "Newer title" in body
    assert "Older title" not in body
    assert body.count("\n- ") == 1


def test_wikilink_only_when_a_note_exists() -> None:
    linked = render_session_index(
        "claude",
        [row(provider="claude", title="Plan", note_stem="plan-note")],
    )
    plain = render_session_index(
        "claude",
        [row(provider="claude", title="Plan", note_stem=None)],
    )
    assert "[[plan-note|Plan]]" in linked
    assert "[[" not in plain
    assert "Plan" in plain


def test_summary_uses_the_first_line_only() -> None:
    body = render_session_index(
        "grok",
        [row(provider="grok", summary="Alpha only\nBeta is a transcript")],
    )
    assert "Alpha only" in body
    assert "Beta" not in body


def test_gemini_rows_say_takeout_day() -> None:
    when = index_date("gemini", "takeout-day:2024-05-01", None)
    body = render_session_index(
        "gemini",
        [
            row(
                provider="gemini",
                provider_conversation_id="takeout-day:2024-05-01",
                title="Gemini activity",
                occurred_on=when,
                summary="Did some things",
            )
        ],
    )
    assert when == date(2024, 5, 1)
    assert "2024-05-01 · takeout-day · Gemini activity · Did some things" in body
    assert "takeout-day:2024-05-01" not in body
    assert re.search(r"\bchat\b", body, re.I) is None
    assert "conversation" not in body


def test_a_long_summary_line_is_truncated() -> None:
    body = render_session_index(
        "chatgpt",
        [row(summary="x" * 500)],
    )
    line = next(part for part in body.splitlines() if part.startswith("- "))
    assert len(line) < 400
    assert line.endswith("...")
