"""Which imported conversations should become Obsidian notes, and where.

Jev decides; this module only applies the policy to its answers:

    score = max(promote_to_obsidian, deep_extraction) + recency bonus

The bonus is PC_VAULT_RECENCY_BONUS for a conversation from today and halves
every PC_VAULT_RECENCY_HALFLIFE_DAYS, so recent work reaches the vault more
easily than old work. A conversation becomes a note when its score reaches
PC_VAULT_MIN_SCORE. Jev also chose the folder (destination) and project.
Highly sensitive conversations never auto-promote.

The threshold is deliberately generous: a vault with many well-linked notes
is what makes Obsidian's graph, backlinks and maps useful.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from personal_context_mcp.config import Settings
from personal_context_mcp.db.pool import Database


@dataclass
class VaultCandidate:
    conversation_id: UUID
    title: str | None
    provider: str
    destination: str
    project: str | None
    promote_p: float
    deep_p: float
    age_days: float
    score: float


def recency_bonus(age_days: float, settings: Settings) -> float:
    return settings.vault_recency_bonus * 0.5 ** (
        max(age_days, 0.0) / settings.vault_recency_halflife_days
    )


def score(promote_p: float, deep_p: float, age_days: float, settings: Settings) -> float:
    return max(promote_p, deep_p) + recency_bonus(age_days, settings)


def _value(answers: Any, key: str) -> float:
    data = answers if isinstance(answers, dict) else json.loads(answers or "{}")
    item = data.get(key) or {}
    try:
        return float(item.get("value") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _choice(answers: Any, key: str) -> str | None:
    data = answers if isinstance(answers, dict) else json.loads(answers or "{}")
    value = (data.get(key) or {}).get("value")
    return str(value) if value else None


async def vault_candidates(db: Database, settings: Settings) -> list[VaultCandidate]:
    rows = await db.fetch(
        """
        SELECT c.id, c.title, c.provider, c.sensitivity::text AS sensitivity,
               coalesce(
                   (SELECT max(m.created_at) FROM messages m WHERE m.conversation_id = c.id),
                   c.started_at, c.created_at
               ) AS happened_at,
               ev.answers
        FROM conversations c
        JOIN LATERAL (
            SELECT e.answers FROM classification_events e
            WHERE e.object_type = 'checkpoint' AND e.object_id = c.id
              AND e.classifier = 'jev' AND NOT e.degraded
            ORDER BY e.created_at DESC LIMIT 1
        ) ev ON true
        WHERE c.imported AND c.sensitivity <> 'highly_sensitive'
        """
    )
    now = datetime.now(UTC)
    chosen: list[VaultCandidate] = []
    for row in rows:
        promote_p = _value(row["answers"], "promote_to_obsidian")
        deep_p = _value(row["answers"], "deep_extraction")
        age = (now - row["happened_at"]).total_seconds() / 86_400
        total = score(promote_p, deep_p, age, settings)
        if total < settings.vault_min_score:
            continue
        destination = _choice(row["answers"], "destination") or "inbox"
        project = _choice(row["answers"], "project")
        chosen.append(
            VaultCandidate(
                conversation_id=row["id"],
                title=row["title"],
                provider=row["provider"],
                destination="inbox" if destination == "archive_only" else destination,
                project=None if project in (None, "unassigned") else project,
                promote_p=promote_p,
                deep_p=deep_p,
                age_days=age,
                score=total,
            )
        )
    return sorted(chosen, key=lambda c: c.score, reverse=True)
