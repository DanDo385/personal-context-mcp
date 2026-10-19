"""Propose a project for sessions saved without one. Read-only; applies nothing.

Most stored session summaries have no project, so they can never appear in a
project's handoff. This reads each untagged summary and proposes a project with
the same deterministic matcher bootstrap uses (names, aliases, repository and
worktree paths, near misspellings), with the reason. A person reviews the list
before anything is written.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from personal_context_mcp.db.pool import Database
from personal_context_mcp.retrieval import inference


@dataclass
class Proposal:
    session_id: UUID
    conversation_id: UUID
    provider: str
    updated_at: datetime
    title: str
    synthetic_id: bool
    imported: bool
    proposed: str | None
    score: float
    reason: str
    candidates: str


def propose(rows: list[Any], projects: list[inference.ProjectRow]) -> list[Proposal]:
    """One proposal per untagged session row. Pure: no database access."""
    out: list[Proposal] = []
    for row in rows:
        questions = " ".join(str(q) for q in (row["open_questions"] or []))
        text = " ".join(
            part
            for part in (row["title"], row["conversation_title"], row["summary"], questions)
            if part
        )
        found = inference.infer(text, projects)
        out.append(
            Proposal(
                session_id=row["id"],
                conversation_id=row["conversation_id"],
                provider=row["provider"],
                updated_at=row["updated_at"],
                title=(row["title"] or row["conversation_title"] or "")[:120],
                synthetic_id=str(row["provider_conversation_id"] or "").startswith("synthetic:"),
                imported=bool(row["imported"]),
                proposed=found.project.slug if found.project else None,
                score=found.score,
                reason=found.reason,
                candidates="|".join(m.project.slug for m in found.candidates),
            )
        )
    return out


async def untagged_sessions(db: Database, *, limit: int | None = None) -> list[Proposal]:
    """Read untagged sessions and the project list in one read-only transaction."""
    async with db.acquire() as conn, conn.transaction(readonly=True):
        projects = [
            inference.ProjectRow.from_record(r)
            for r in await conn.fetch(
                "SELECT id, slug, name, status, metadata FROM projects ORDER BY last_active_at DESC"
            )
        ]
        rows = await conn.fetch(
            """
            SELECT s.id, s.conversation_id, s.provider, s.updated_at, s.title, s.summary,
                   s.open_questions, c.title AS conversation_title,
                   c.provider_conversation_id, c.imported
            FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
            WHERE s.project_id IS NULL AND c.project_id IS NULL
            ORDER BY s.updated_at DESC
            LIMIT $1
            """,
            limit,
        )
    return propose(list(rows), projects)


def write_csv(proposals: list[Proposal], path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Proposal.__dataclass_fields__))
        writer.writeheader()
        for proposal in proposals:
            writer.writerow(asdict(proposal))
    by_project = Counter(p.proposed for p in proposals if p.proposed)
    return {
        "untagged_sessions": len(proposals),
        "proposed": sum(by_project.values()),
        "ambiguous": sum(1 for p in proposals if not p.proposed and "|" in p.candidates),
        "weak_hint": sum(
            1 for p in proposals if not p.proposed and p.candidates and "|" not in p.candidates
        ),
        "no_match": sum(1 for p in proposals if not p.proposed and not p.candidates),
        "synthetic_ids": sum(p.synthetic_id for p in proposals),
        "by_project": dict(by_project.most_common()),
        "written_to": str(path),
    }
