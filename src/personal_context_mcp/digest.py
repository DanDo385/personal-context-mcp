"""A readable digest of Personal Context, for assistants that cannot connect.

Some assistants (Gemini on plans without custom apps, for example) cannot
reach the MCP server. The digest gives them the same context as a document:
who the person is and how they like to work, active projects, recent
decisions, open tasks, recent sessions and open questions.

It is built only from stored rows, never by a model, and it leaves out
anything at a sensitivity level listed in PC_NO_EXTERNAL_SENSITIVITY,
because the document is meant to be handed to a third-party service.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_context_mcp.config import Settings
from personal_context_mcp.db.pool import Database
from personal_context_mcp.util import truncate_for_budget

PREAMBLE = """\
This document is {owner}'s Personal Context: their shared memory across ChatGPT,
Claude, Gemini and Grok, regenerated automatically on {date}. Treat it as
background knowledge about them. Prefer it over guesses, prefer what they say in
the current conversation over it, and do not quote it back unless asked.
Items marked (you said) are the person's own words; everything else was recorded
by an assistant."""


def _clip(text: str, max_chars: int) -> str:
    """truncate_for_budget counts tokens (~3.6 characters each)."""
    return truncate_for_budget(text, int(max_chars / 3.6))


def _flat(text: str | None, max_chars: int = 600) -> str:
    """Stored text on one line. Summaries of imported chats are raw excerpts
    that can contain Markdown headings and lists, which would otherwise break
    the digest's own structure."""
    one_line = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*#+\s*", "", text or "")).strip()
    return _clip(one_line, max_chars)


def _said(provenance: str | None) -> str:
    return " (you said)" if provenance == "user_statement" else ""


async def build_digest(
    db: Database,
    settings: Settings,
    *,
    owner: str = "the user",
    recent_days: int = 14,
    max_chars: int = 40_000,
) -> str:
    blocked = list(settings.no_external_sensitivity)
    since = datetime.now(UTC) - timedelta(days=recent_days)
    sections: list[str] = [
        PREAMBLE.format(owner=owner, date=datetime.now(UTC).strftime("%Y-%m-%d")),
    ]

    memories = await db.fetch(
        """
        SELECT m.kind, m.subject, m.content, m.provenance::text AS provenance
        FROM memories m
        WHERE m.status = 'active' AND NOT (m.sensitivity::text = ANY($1::text[]))
        ORDER BY (m.kind = 'preference') DESC, m.importance DESC NULLS LAST, m.updated_at DESC
        LIMIT 80
        """,
        blocked,
    )
    preferences = [m for m in memories if m["kind"] == "preference"]
    facts = [m for m in memories if m["kind"] != "preference"]
    if preferences:
        sections.append(
            "## How they want to work\n\n"
            + "\n".join(
                f"- {_flat(m['content'], 400)}{_said(m['provenance'])}" for m in preferences
            )
        )
    if facts:
        sections.append(
            "## Things to know\n\n"
            + "\n".join(f"- {_flat(m['content'], 400)}{_said(m['provenance'])}" for m in facts)
        )

    projects = await db.fetch(
        """
        SELECT p.id, p.name, p.status, p.description, p.goals
        FROM projects p
        WHERE p.status IN ('active', 'paused') AND NOT (p.sensitivity::text = ANY($1::text[]))
        ORDER BY p.last_active_at DESC NULLS LAST
        LIMIT 25
        """,
        blocked,
    )
    if projects:
        lines = ["## Projects"]
        for p in projects:
            lines.append(f"\n### {_flat(p['name'], 120)} ({p['status']})")
            for label, value in (("About", p["description"]), ("Goals", p["goals"])):
                if value:
                    lines.append(f"{label}: {_flat(value, 400)}")
            decided = await db.fetch(
                """
                SELECT statement, provenance::text AS provenance FROM decisions
                WHERE project_id = $1 AND status = 'active'
                ORDER BY decided_at DESC NULLS LAST, created_at DESC LIMIT 5
                """,
                p["id"],
            )
            for d in decided:
                lines.append(f"- Decided: {_flat(d['statement'], 300)}{_said(d['provenance'])}")
        sections.append("\n".join(lines))

    loose = await db.fetch(
        """
        SELECT statement, rationale, provenance::text AS provenance FROM decisions
        WHERE project_id IS NULL AND status = 'active'
        ORDER BY decided_at DESC NULLS LAST, created_at DESC LIMIT 20
        """
    )
    if loose:
        sections.append(
            "## Other recent decisions\n\n"
            + "\n".join(
                f"- {_flat(d['statement'], 300)}"
                + (f" Why: {_flat(d['rationale'], 300)}" if d["rationale"] else "")
                + _said(d["provenance"])
                for d in loose
            )
        )

    items = await db.fetch(
        """
        SELECT l.name AS list_name, i.text, i.due_date
        FROM list_items i JOIN lists l ON l.id = i.list_id
        WHERE l.status = 'active' AND i.status IN ('open', 'blocked')
        ORDER BY l.name, i.priority DESC, i.position
        LIMIT 80
        """
    )
    if items:
        lines = ["## Open lists"]
        current = None
        for item in items:
            if item["list_name"] != current:
                current = item["list_name"]
                lines.append(f"\n### {current}")
            due = f" (due {item['due_date']:%Y-%m-%d})" if item["due_date"] else ""
            lines.append(f"- [ ] {_flat(item['text'], 200)}{due}")
        sections.append("\n".join(lines))

    # "Recent" means when the conversation happened, not when its summary row
    # was last touched: an import refreshes every row it sees.
    sessions = await db.fetch(
        """
        SELECT * FROM (
            SELECT s.title, s.summary, s.provider, s.open_questions,
                   coalesce(
                       (SELECT max(m.created_at) FROM messages m WHERE m.conversation_id = c.id),
                       c.started_at, s.updated_at
                   ) AS happened_at
            FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
            WHERE NOT (s.sensitivity::text = ANY($2::text[]))
        ) recent
        WHERE happened_at >= $1
        ORDER BY happened_at DESC
        LIMIT 25
        """,
        since,
        blocked,
    )
    if sessions:
        lines = [f"## Recent conversations (last {recent_days} days)"]
        questions: list[str] = []
        for s in sessions:
            lines.append(
                f"\n### {_flat(s['title'] or 'Untitled', 120)} ({s['provider']}, {s['happened_at']:%Y-%m-%d})\n"
                f"{_flat(s['summary'], 600)}"
            )
            questions.extend(str(q) for q in (s["open_questions"] or []))
        sections.append("\n".join(lines))
        if questions:
            sections.append(
                "## Open questions\n\n"
                + "\n".join(f"- {_flat(q, 300)}" for q in dict.fromkeys(questions))
            )

    return _clip("\n\n".join(sections).strip(), max_chars) + "\n"


def digest_stats(text: str) -> dict[str, Any]:
    return {"characters": len(text), "sections": text.count("\n## ")}
