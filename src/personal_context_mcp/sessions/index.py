"""Session index notes, built from summaries already stored.

One note per provider. No model is asked, and no conversation becomes its
own note. The body is a capped list: title, date, and the summary's first
line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from personal_context_mcp.importers.sensitivity import raise_to
from personal_context_mcp.obsidian.render import wikilink

INDEX_CAP = 200
SUMMARY_LINE_LIMIT = 240

#: (provider id, note title). Always these four, in this order.
PROVIDER_TITLES: tuple[tuple[str, str], ...] = (
    ("chatgpt", "ChatGPT"),
    ("claude", "Claude"),
    ("grok", "Grok"),
    ("gemini", "Gemini"),
)

_LEVELS = frozenset({"normal", "private", "highly_sensitive"})


@dataclass(frozen=True)
class SessionIndexRow:
    """One conversation, keyed by ``(provider, provider_conversation_id)``."""

    provider: str
    provider_conversation_id: str
    title: str
    occurred_on: date | None
    summary: str | None
    message_count: int
    note_stem: str | None = None
    sensitivity: str = "normal"


def index_date(
    provider: str, provider_conversation_id: str, started_at: datetime | date | None
) -> date | None:
    """Date shown on an index row.

    Gemini rows are takeout days. The day is the suffix of
    ``takeout-day:YYYY-MM-DD`` when that id is present.
    """
    if provider == "gemini" and provider_conversation_id.startswith("takeout-day:"):
        raw = provider_conversation_id.split(":", 1)[1][:10]
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    if isinstance(started_at, datetime):
        return started_at.date()
    if isinstance(started_at, date):
        return started_at
    return None


def first_summary_line(summary: str | None, *, limit: int = SUMMARY_LINE_LIMIT) -> str | None:
    if not summary:
        return None
    line = summary.strip().splitlines()[0].strip() if summary.strip() else ""
    line = " ".join(line.split())
    if not line:
        return None
    if len(line) > limit:
        line = line[: limit - 3].rstrip() + "..."
    return line


def _plain_title(title: str) -> str:
    clean = " ".join((title or "").split())
    clean = clean.replace("[", "").replace("]", "").replace("|", "-").strip()
    return clean or "Untitled"


def _outranks(candidate: SessionIndexRow, current: SessionIndexRow) -> bool:
    if bool(candidate.note_stem) != bool(current.note_stem):
        return candidate.note_stem is not None
    return (candidate.occurred_on or date.min) >= (current.occurred_on or date.min)


def prepare_session_index(
    rows: list[SessionIndexRow] | tuple[SessionIndexRow, ...], *, cap: int = INDEX_CAP
) -> tuple[list[SessionIndexRow], int]:
    """Drop empty conversations, keep one row per provider id, cap at ``cap``.

    Returns ``(shown, older)``. ``older`` is how many kept rows did not fit.
    Zero-message rows are neither shown nor counted as older.
    """
    best: dict[tuple[str, str], SessionIndexRow] = {}
    for row in rows:
        if row.message_count <= 0 or not row.provider_conversation_id:
            continue
        key = (row.provider, row.provider_conversation_id)
        current = best.get(key)
        if current is None or _outranks(row, current):
            best[key] = row
    ordered = sorted(
        best.values(),
        key=lambda row: (row.occurred_on or date.min, row.provider_conversation_id),
        reverse=True,
    )
    shown = ordered[:cap]
    return shown, len(ordered) - len(shown)


def index_sensitivity(
    rows: list[SessionIndexRow] | tuple[SessionIndexRow, ...], *, cap: int = INDEX_CAP
) -> str:
    """Strictest sensitivity among the rows whose text is actually written."""
    shown, _older = prepare_session_index(rows, cap=cap)
    level = "normal"
    for row in shown:
        if row.sensitivity in _LEVELS:
            level = raise_to(level, row.sensitivity)
    return level


def _entry_line(provider: str, row: SessionIndexRow) -> str:
    when = row.occurred_on.isoformat() if row.occurred_on else "undated"
    title = _plain_title(row.title)
    shown = wikilink(row.note_stem, title) if row.note_stem else title
    parts = [when]
    if provider == "gemini":
        parts.append("takeout-day")
    parts.append(shown)
    line = first_summary_line(row.summary)
    if line:
        parts.append(line)
    return "- " + " · ".join(parts)


def render_session_index(
    provider: str,
    rows: list[SessionIndexRow] | tuple[SessionIndexRow, ...],
    *,
    cap: int = INDEX_CAP,
) -> str:
    """Markdown body for one provider index. The note title is added at render."""
    shown, older = prepare_session_index(rows, cap=cap)
    if provider == "gemini":
        intro = "Each row is one takeout day."
        empty = "No takeout days with messages."
    else:
        intro = "Each row is one conversation."
        empty = "No conversations with messages."
    if not shown:
        return f"{intro}\n\n{empty}\n"
    lines = [intro, "", *(_entry_line(provider, row) for row in shown)]
    if older:
        lines.extend(["", f"{older} older"])
    lines.append("")
    return "\n".join(lines)
