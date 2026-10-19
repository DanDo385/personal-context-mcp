"""Synthetic retrieval fixture: one invented person ("Sam") and five invented projects.

Nothing here is real personal data. Every row has a stable key, and its id is
``uuid5(NAMESPACE, key)``, so the eval cases in ``retrieval_cases.yaml`` can name
the items they expect by key and an exact-id lookup can be tested.

Seeding writes SQL directly because the cases depend on things the write tools
cannot set: past dates, provenance, superseded links and sessions with no
project. This module is the only place the eval knows the schema; the cases
and assertions only look at what the read tools return.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

NAMESPACE = UUID("6f1c2a52-2f0e-4c39-9d4b-0b1f5c2c9e11")


def key_id(key: str) -> UUID:
    return uuid5(NAMESPACE, key)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Seeded:
    now: datetime
    ids: dict[str, UUID] = field(default_factory=dict)

    def key_for(self, item_id: str | UUID) -> str | None:
        wanted = UUID(str(item_id))
        for key, value in self.ids.items():
            if value == wanted:
                return key
        return None


PROJECTS: list[dict[str, Any]] = [
    {
        "key": "p_harbor",
        "slug": "harbor-notes",
        "name": "Harbor Notes",
        "description": "A note-taking app that syncs between phone and laptop.",
        "aliases": ["harbor", "hn app"],
        "active_days_ago": 0.4,
    },
    {
        "key": "p_garden",
        "slug": "garden-planner",
        "name": "Garden Planner",
        "description": "Planning the vegetable garden.",
        "aliases": [],
        "active_days_ago": 2,
    },
    {
        "key": "p_lighthouse",
        "slug": "lighthouse-api",
        "name": "Lighthouse API",
        "description": "Public HTTP API for the lighthouse status board.",
        "aliases": ["lh api"],
        "active_days_ago": 6,
    },
    {
        "key": "p_atlas_ledger",
        "slug": "atlas-ledger",
        "name": "Atlas Ledger",
        "description": "Bookkeeping for the Atlas side business.",
        "aliases": [],
        "active_days_ago": 9,
    },
    {
        "key": "p_atlas_mobile",
        "slug": "atlas-mobile",
        "name": "Atlas Mobile",
        "description": "Phone app for the Atlas side business.",
        "aliases": [],
        "active_days_ago": 11,
    },
    {
        "key": "p_blog",
        "slug": "old-blog",
        "name": "Old Blog",
        "description": "The retired personal blog.",
        "aliases": [],
        "active_days_ago": 200,
        "status": "archived",
    },
]

DECISIONS: list[dict[str, Any]] = [
    {
        "key": "d_hn_alone",
        "project": "p_harbor",
        "days_ago": 20,
        "statement": "Harbor Notes v2 is for Sam alone; open-source friendliness is not a constraint.",
    },
    {
        "key": "d_hn_sqlite",
        "project": "p_harbor",
        "days_ago": 30,
        "statement": "Store Harbor Notes data in SQLite.",
        "status": "superseded",
    },
    {
        "key": "d_hn_postgres",
        "project": "p_harbor",
        "days_ago": 10,
        "statement": "Store Harbor Notes data in Postgres instead of SQLite.",
        "rationale": "Several devices write at once.",
        "supersedes": "d_hn_sqlite",
    },
    {
        "key": "d_hn_fly",
        "project": "p_harbor",
        "days_ago": 15,
        "statement": "Deploy the Harbor Notes server on Fly.io.",
    },
    {
        "key": "d_hn_render",
        "project": "p_harbor",
        "days_ago": 5,
        "statement": "Deploy the Harbor Notes server on Render instead of Fly.io.",
    },
    {
        "key": "d_hn_merge",
        "project": "p_harbor",
        "days_ago": 3,
        "statement": "Agents merge their own worktree branches into main once the full test suite passes.",
    },
    {
        "key": "d_hn_cron",
        "project": "p_harbor",
        "days_ago": 2,
        "statement": "Run a nightly cron job that compacts the Harbor Notes sync log.",
        "provenance": "assistant_generated",
    },
    {
        "key": "d_gp_beds",
        "project": "p_garden",
        "days_ago": 8,
        "statement": "Grow the tomatoes in raised beds this year.",
    },
    {
        "key": "d_lh_rate",
        "project": "p_lighthouse",
        "days_ago": 12,
        "statement": "The Lighthouse API rate limit is 100 requests per minute per key (ticket LH-142).",
    },
    {
        "key": "d_al_currency",
        "project": "p_atlas_ledger",
        "days_ago": 9,
        "statement": "Atlas Ledger keeps every account in euros.",
    },
]

#: Each summary gets its own conversation. ``project`` None means untagged.
SUMMARIES: list[dict[str, Any]] = [
    {
        "key": "s_hn_untagged",
        "project": None,
        "provider": "grok",
        "days_ago": 0.5,
        "title": "Harbor Notes search bug",
        "summary": "Debugged why Harbor Notes search returned nothing for long questions.",
        "open_questions": ["Is the tokenizer dropping hyphenated words?"],
        "synthetic_id": True,
    },
    {
        "key": "s_hn_sync",
        "project": "p_harbor",
        "provider": "claude",
        "days_ago": 1,
        "title": "Sync conflict handling",
        "summary": (
            "Worked through how Harbor Notes resolves edit conflicts between phone and "
            "laptop. Chose last-writer-wins per paragraph."
        ),
        "open_questions": ["Should deleted paragraphs leave a tombstone?"],
    },
    {
        "key": "s_hn_opensource",
        "project": "p_harbor",
        "provider": "chatgpt",
        "days_ago": 4,
        "title": "Open-sourcing plan",
        "summary": (
            "Sam now wants Harbor Notes v2 open-sourced when it is finished, so the "
            "docs must be friendly to beginners."
        ),
        "open_questions": ["Which license, MIT or Apache-2.0?"],
    },
    {
        "key": "s_hn_render",
        "project": "p_harbor",
        "provider": "gemini",
        "days_ago": 7,
        "title": "Render deploy",
        "summary": "Moved the Harbor Notes server hosting to Render and checked the health route.",
    },
    {
        "key": "s_hn_schema",
        "project": "p_harbor",
        "provider": "claude",
        "days_ago": 25,
        "title": "Initial schema sketch",
        "summary": "Sketched the first Harbor Notes tables for notes and devices.",
        "open_questions": ["Do devices need their own table?"],
    },
    {
        "key": "s_gp_seeds",
        "project": "p_garden",
        "provider": "claude",
        "days_ago": 2,
        "title": "Seed order",
        "summary": "Ordered tomato and basil seeds for the Garden Planner beds.",
    },
    {
        "key": "s_al_tax",
        "project": "p_atlas_ledger",
        "provider": "chatgpt",
        "days_ago": 9,
        "title": "Quarterly tax",
        "summary": "Prepared the Atlas Ledger quarterly tax estimate.",
        "open_questions": ["Is the home office deductible?"],
    },
    {
        "key": "s_am_push",
        "project": "p_atlas_mobile",
        "provider": "claude",
        "days_ago": 11,
        "title": "Push notifications",
        "summary": "Added push notifications to Atlas Mobile.",
    },
    {
        "key": "s_noise_soup",
        "project": None,
        "provider": "chatgpt",
        "days_ago": 1.5,
        "title": "Lentil soup",
        "summary": "A lentil soup recipe with cumin and lemon.",
    },
    {
        "key": "s_noise_bike",
        "project": None,
        "provider": "gemini",
        "days_ago": 3,
        "title": "Bike tyre",
        "summary": "Fixed a slow puncture on the road bike tyre.",
    },
    {
        "key": "s_noise_trip",
        "project": None,
        "provider": "claude",
        "days_ago": 6,
        "title": "Weekend trip",
        "summary": "Planned a weekend trip to the coast with train times.",
    },
]

MEMORIES: list[dict[str, Any]] = [
    {
        "key": "m_pref_plain",
        "content": "Sam prefers plain explanations over jargon.",
        "kind": "preference",
        "provenance": "user_statement",
        "days_ago": 40,
    },
    {
        "key": "m_hn_stack",
        "project": "p_harbor",
        "content": "Harbor Notes is written in TypeScript with a Go sync server.",
        "provenance": "user_statement",
        "days_ago": 18,
    },
    {
        "key": "m_hn_import",
        "project": "p_harbor",
        "content": (
            "Sync conflict handling: worked through how Harbor Notes resolves edit "
            "conflicts between phone and laptop."
        ),
        "provenance": "imported_metadata",
        "days_ago": 1,
    },
    {
        "key": "m_espresso",
        "content": "The espresso grinder setting is 12 for the dark roast.",
        "provenance": "assistant_generated",
        "days_ago": 14,
    },
    {
        "key": "m_sister",
        "content": "Sam's sister lives in Lisbon.",
        "kind": "relationship",
        "provenance": "user_statement",
        "days_ago": 60,
    },
    {
        "key": "m_brother",
        "content": "Sam's brother lives in Porto.",
        "kind": "relationship",
        "provenance": "user_statement",
        "days_ago": 60,
    },
    {
        "key": "m_gp_frost",
        "project": "p_garden",
        "content": "The last spring frost in Sam's garden is usually mid April.",
        "provenance": "user_statement",
        "days_ago": 30,
    },
]

NOTES: list[dict[str, Any]] = [
    {
        "key": "n_crdt",
        "project": "p_harbor",
        "title": "CRDT versus last-writer-wins",
        "content": (
            "Research: CRDTs merge concurrent edits without conflicts but cost storage; "
            "last-writer-wins is simpler and loses the older edit."
        ),
        "note_class": "research",
        "provenance": "external_source",
        "days_ago": 6,
    },
    {
        "key": "n_companion",
        "project": "p_garden",
        "title": "Companion planting",
        "content": "Basil grows well next to tomatoes and keeps some pests away.",
        "note_class": "research",
        "provenance": "assistant_generated",
        "days_ago": 20,
    },
]

LISTS: list[dict[str, Any]] = [
    {
        "key": "l_hn_tasks",
        "slug": "harbor-notes-tasks",
        "name": "Harbor Notes tasks",
        "list_type": "task",
        "project": "p_harbor",
        "items": [
            {"key": "t_hn_guide", "text": "Write the beginner setup guide", "days_ago": 4},
            {"key": "t_hn_tomb", "text": "Add tombstones for deleted paragraphs", "days_ago": 1},
            {
                "key": "t_hn_host",
                "text": "Pick a hosting provider",
                "status": "done",
                "days_ago": 7,
            },
        ],
    },
    {
        "key": "l_reading",
        "slug": "reading-list",
        "name": "Reading list",
        "list_type": "reading",
        "items": [
            {"key": "r_ddia", "text": "Designing Data-Intensive Applications", "days_ago": 30},
            {"key": "r_pragprog", "text": "The Pragmatic Programmer", "days_ago": 25},
        ],
    },
    {
        "key": "l_groceries",
        "slug": "groceries",
        "name": "Groceries",
        "list_type": "shopping",
        "items": [
            {"key": "g_oat", "text": "Oat milk", "days_ago": 1},
            {"key": "g_basil", "text": "Fresh basil", "days_ago": 1},
        ],
    },
]

#: An imported transcript whose words appear nowhere in the structured layers.
RAW_CONVERSATION = {
    "key": "c_zebrafish",
    "provider": "chatgpt",
    "days_ago": 50,
    "title": "Aquarium questions",
    "messages": [
        ("m_zebra_user", "user", "How do I calibrate the zebrafish tank thermometer?"),
        (
            "m_zebra_reply",
            "assistant",
            "Put the thermometer in ice water and check it reads zero before trusting it.",
        ),
    ],
}


async def seed(db: Any, now: datetime | None = None) -> Seeded:
    """Insert the fixture. ``db`` is the project's Database wrapper."""
    now = now or datetime.now(UTC)
    seeded = Seeded(now=now)

    def at(days_ago: float) -> datetime:
        return now - timedelta(days=days_ago)

    def remember(key: str) -> UUID:
        seeded.ids[key] = key_id(key)
        return seeded.ids[key]

    async with db.transaction() as conn:
        for p in PROJECTS:
            await conn.execute(
                """
                INSERT INTO projects (id, slug, name, description, status, last_active_at,
                                      created_at, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $6, $7)
                """,
                remember(p["key"]),
                p["slug"],
                p["name"],
                p["description"],
                p.get("status", "active"),
                at(p["active_days_ago"]),
                json.loads(json.dumps({"aliases": p["aliases"]})),
            )

        for d in DECISIONS:
            when = at(d["days_ago"])
            await conn.execute(
                """
                INSERT INTO decisions (id, statement, statement_sha256, rationale, project_id,
                                       source_provider, provenance, status, supersedes_id,
                                       decided_at, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, 'claude', $6, $7, $8, $9, $9, $9)
                """,
                remember(d["key"]),
                d["statement"],
                _sha(d["statement"]),
                d.get("rationale"),
                seeded.ids[d["project"]],
                d.get("provenance", "user_statement"),
                d.get("status", "active"),
                seeded.ids.get(d["supersedes"]) if d.get("supersedes") else None,
                when,
            )

        for s in SUMMARIES:
            when = at(s["days_ago"])
            project_id = seeded.ids[s["project"]] if s["project"] else None
            conv_id = remember("c_" + s["key"])
            ext_id = (
                f"synthetic:{s['provider']}:{s['key']}"
                if s.get("synthetic_id")
                else f"{s['provider']}-{s['key']}"
            )
            await conn.execute(
                """
                INSERT INTO conversations (id, provider, provider_conversation_id, title,
                                           project_id, started_at, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $6, $6)
                """,
                conv_id,
                s["provider"],
                ext_id,
                s["title"],
                project_id,
                when,
            )
            await conn.execute(
                """
                INSERT INTO session_summaries (id, conversation_id, provider, project_id, title,
                                               summary, summary_sha256, open_questions,
                                               created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
                """,
                remember(s["key"]),
                conv_id,
                s["provider"],
                project_id,
                s["title"],
                s["summary"],
                _sha(s["summary"]),
                s.get("open_questions", []),
                when,
            )

        for m in MEMORIES:
            when = at(m["days_ago"])
            await conn.execute(
                """
                INSERT INTO memories (id, content, content_sha256, kind, project_id, importance,
                                      provenance, source_provider, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, 0.6, $6, 'claude', $7, $7)
                """,
                remember(m["key"]),
                m["content"],
                _sha(m["content"]),
                m.get("kind", "fact"),
                seeded.ids[m["project"]] if m.get("project") else None,
                m["provenance"],
                when,
            )

        for n in NOTES:
            when = at(n["days_ago"])
            await conn.execute(
                """
                INSERT INTO notes (id, slug, title, content, content_sha256, note_class,
                                   project_id, provenance, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
                """,
                remember(n["key"]),
                n["key"].replace("_", "-"),
                n["title"],
                n["content"],
                _sha(n["content"]),
                n["note_class"],
                seeded.ids[n["project"]] if n.get("project") else None,
                n["provenance"],
                when,
            )

        for lst in LISTS:
            list_id = remember(lst["key"])
            await conn.execute(
                """
                INSERT INTO lists (id, slug, name, list_type, project_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                list_id,
                lst["slug"],
                lst["name"],
                lst["list_type"],
                seeded.ids[lst["project"]] if lst.get("project") else None,
            )
            for position, item in enumerate(lst["items"]):
                when = at(item["days_ago"])
                await conn.execute(
                    """
                    INSERT INTO list_items (id, list_id, text, text_sha256, status, position,
                                            provenance, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, 'user_statement', $7, $7)
                    """,
                    remember(item["key"]),
                    list_id,
                    item["text"],
                    _sha(item["text"].lower()),
                    item.get("status", "open"),
                    position,
                    when,
                )

        raw = RAW_CONVERSATION
        when = at(raw["days_ago"])
        conv_id = remember(raw["key"])
        await conn.execute(
            """
            INSERT INTO conversations (id, provider, provider_conversation_id, title, imported,
                                       started_at, created_at, updated_at)
            VALUES ($1, $2, $3, $4, true, $5, $5, $5)
            """,
            conv_id,
            raw["provider"],
            "import-" + raw["key"],
            raw["title"],
            when,
        )
        for sequence, (key, role, content) in enumerate(raw["messages"]):
            await conn.execute(
                """
                INSERT INTO messages (id, conversation_id, provider, role, content,
                                      content_sha256, provenance, sequence, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                remember(key),
                conv_id,
                raw["provider"],
                role,
                content,
                _sha(content),
                "imported_metadata",
                sequence,
                when,
            )

    return seeded
