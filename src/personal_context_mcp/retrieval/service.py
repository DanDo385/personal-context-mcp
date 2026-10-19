"""Context retrieval: ranked search, and the package that starts a chat.

Search (``context_search``) runs one SQL statement over every structured
layer. The query is parsed in Python (``retrieval/query.py``): content words
are ORed, rows that match more of them score higher, and quoted phrases,
``-negation``, exact ids and the project filter keep their meaning. Ranking is
text match times who said it (the user's own words above an assistant's
summary, imported text lowest) times recency.

Bootstrap (``context_bootstrap``) fills a token budget in this order:

    1 handoff    the project's newest session summaries with their open
                 questions, open tasks and list items, and active decisions
    2 relevant   items matching the query, ranked as above
    3 raw        transcript excerpts, only when 1 and 2 found nothing

The project is the one passed, or one read from the query with the reason
said out loud; when the query could mean two projects, both get a short
handoff. Decisions that a newer item may overrule are flagged, never silently
dropped or silently preferred. Every layer reports whether it ran and why.

Nothing on these paths calls a model, the classifier or the vault: a few SQL
statements and some arithmetic.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from personal_context_mcp.config import Settings
from personal_context_mcp.db.pool import Database
from personal_context_mcp.embeddings.service import EmbeddingService
from personal_context_mcp.models import (
    ConflictReport,
    ContextItem,
    ContextPackage,
    LayerReport,
    ProjectCandidate,
    ProjectChoice,
    Provenance_,
)
from personal_context_mcp.projects.service import ProjectService
from personal_context_mcp.retrieval import inference
from personal_context_mcp.retrieval.query import ParsedQuery, parse
from personal_context_mcp.sessions.service import question_key
from personal_context_mcp.util import estimate_tokens, truncate_for_budget

#: Per-item ceiling so one enormous note cannot eat the whole package.
_MAX_ITEM_TOKENS = 450
#: Starting estimate for the package's own fields (project, layers, notes).
#: The finished package is measured and trimmed to the budget anyway.
_PACKAGE_OVERHEAD = 110
#: Below this budget, items leave out fields the package already implies.
_COMPACT_BELOW = 400
#: Trim order when the measured package is over budget: highest tier goes first.
_TIER_CORE = 0
_TIER_RELEVANT_TOP = 1
_TIER_HANDOFF_REST = 2
_TIER_RELEVANT_REST = 3
_TIER_RAW = 4

#: Who said it. The user's own words outrank a model's conclusions; text
#: lifted from imported chats (titles, transcript openings) ranks lowest.
PROVENANCE_WEIGHT = {
    "user_statement": 1.0,
    "system": 0.9,
    "tool_result": 0.8,
    "external_source": 0.8,
    "assistant_generated": 0.7,
    "imported_metadata": 0.45,
}
KIND_WEIGHT = {
    "decision": 1.0,
    "memory": 0.95,
    "list_item": 0.9,
    "note": 0.85,
    "artifact": 0.8,
    "summary": 0.8,
    "message": 0.5,
}
#: Recency half-weight age: an item this many days old keeps 75% of its score.
_RECENCY_DAYS = 90.0

_NOTHING_MATCHED = (
    "Nothing in Personal Context matched. Answer from the current conversation; "
    "do not assume prior context exists."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _recency(when: datetime | None) -> float:
    if when is None:
        return 0.75
    age = max(0.0, (_now() - when).total_seconds() / 86400)
    return 0.5 + 0.5 * math.exp(-age / _RECENCY_DAYS)


def _item(
    *,
    kind: str,
    row: asyncpg.Record | dict[str, Any],
    content: str,
    title: str | None = None,
    score: float | None = None,
    provenance_kind: str = "assistant_generated",
    max_tokens: int = _MAX_ITEM_TOKENS,
    layer: str | None = None,
) -> ContextItem:
    trimmed = truncate_for_budget(content.strip(), max_tokens)
    questions = [str(q) for q in (_get(row, "open_questions") or []) if str(q).strip()]
    status = _get(row, "status")
    effective = _get(row, "effective_at")
    recorded = effective or _get(row, "updated_at") or _get(row, "created_at")
    item = ContextItem(
        type=kind,  # type: ignore[arg-type]
        id=row["id"],
        title=title,
        content=trimmed,
        score=round(score, 4) if score is not None else None,
        project=_get(row, "project_slug"),
        provenance=Provenance_(
            kind=_get(row, "provenance") or provenance_kind,  # type: ignore[arg-type]
            provider=_get(row, "source_provider") or _get(row, "provider"),
            conversation_id=_get(row, "source_conversation_id") or _get(row, "conversation_id"),
            recorded_at=recorded.replace(microsecond=0) if recorded else None,
            confidence=_get(row, "confidence"),
        ),
        effective_at=effective,
        layer=layer,
        status="superseded" if kind == "decision" and status == "superseded" else None,
        superseded_by=_get(row, "superseded_by"),
        open_questions=questions,
        reason=_get(row, "reason"),
    )
    item.estimated_tokens = _cost(item)
    return item


def _get(row: asyncpg.Record | dict[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _wire_tokens(model: ContextItem | ContextPackage) -> int:
    """Tokens the object costs as sent: the tool's JSON at 3.6 characters a token."""
    # Imported here: the mcp package imports the tools, which import services.
    from personal_context_mcp.mcp.compact import dump

    return estimate_tokens(json.dumps(dump(model)))


def _cost(item: ContextItem) -> int:
    return _wire_tokens(item)


# --------------------------------------------------------------- the SQL

#: Columns every search branch returns, in this order.
_RANK_COLS = """
    coalesce(ts_rank_cd({tsv}, q.r), 0)::float8 AS rank,
    (SELECT count(*) FROM unnest($3::text[]) t
      WHERE {tsv} @@ to_tsquery('english', t))::int AS coverage,
    coalesce({tsv} @@ q.ph, false) AS phrase_hit,
    ({id} = ANY($5::uuid[])) AS exact
"""

#: The project filter: tagged to the project, or untagged but naming it.
_IN_PROJECT = """
    ($6::uuid IS NULL
     OR {pid} = $6
     OR ($10::bool AND {pid} IS NULL)
     OR ({pid} IS NULL AND q.pn IS NOT NULL AND {tsv} @@ q.pn))
"""


def _branch(select: str, tsv: str, id_col: str, pid: str) -> str:
    rank = _RANK_COLS.format(tsv=tsv, id=id_col)
    scope = _IN_PROJECT.format(pid=pid, tsv=tsv)
    return select.format(rank=rank, scope=scope, tsv=tsv, id=id_col)


# Built once from constant fragments; caller input only ever arrives as $n.
_SEARCH_SQL = "".join(
    (
        """
WITH q AS (
    SELECT to_tsquery('english', NULLIF($1, '')) AS m,
           to_tsquery('english', NULLIF($2, '')) AS r,
           to_tsquery('english', NULLIF($4, '')) AS ph,
           to_tsquery('english', NULLIF($11, '')) AS pn
)
SELECT u.*, p.slug AS project_slug
FROM (
""",
        _branch(
            """
    (SELECT 'memory'::text AS kind, x.id, NULL::text AS title, x.content AS body,
            x.provenance::text AS provenance, x.project_id, x.updated_at AS effective_at,
            NULL::text AS status, NULL::uuid AS superseded_by,
            x.source_provider AS provider, x.source_conversation_id AS conversation_id,
            x.confidence::float8 AS confidence, NULL::jsonb AS open_questions,
            {rank}
     FROM memories x, q
     WHERE x.status = 'active' AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'decision', x.id, NULL, x.statement || coalesce(E'\\n\\nWhy: ' || x.rationale, ''),
            x.provenance::text, x.project_id, x.decided_at, x.status,
            (SELECT o.id FROM decisions o WHERE o.supersedes_id = x.id
              ORDER BY o.decided_at DESC LIMIT 1),
            x.source_provider, x.source_conversation_id, NULL::float8, NULL::jsonb,
            {rank}
     FROM decisions x, q
     WHERE x.status IN ('active', 'superseded')
       AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'summary', x.id, x.title, x.summary, x.provenance::text,
            coalesce(x.project_id, c.project_id), x.updated_at, NULL, NULL,
            x.provider, x.conversation_id, NULL, x.open_questions,
            {rank}
     FROM session_summaries x JOIN conversations c ON c.id = x.conversation_id, q
     WHERE (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND ($7::text IS NULL OR x.provider = $7)
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "coalesce(x.project_id, c.project_id)",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'note', x.id, x.title, x.content, x.provenance::text, x.project_id, x.updated_at,
            NULL, NULL, x.source_provider, x.source_conversation_id, NULL, NULL,
            {rank}
     FROM notes x, q
     WHERE x.note_class <> 'session_index'
       AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'list_item', x.id, l.name, x.text || coalesce(E'\\n' || x.notes, ''),
            x.provenance::text, l.project_id, x.updated_at, NULL, NULL,
            x.source_provider, x.source_conversation_id, NULL, NULL,
            {rank}
     FROM list_items x JOIN lists l ON l.id = x.list_id, q
     WHERE l.status = 'active' AND x.status IN ('open', 'blocked')
       AND ((x.search_tsv || l.search_tsv) @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC, x.position LIMIT $8)
""",
            "(x.search_tsv || l.search_tsv)",
            "x.id",
            "l.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'artifact', x.id, x.title, coalesce(x.description, x.title), 'system',
            x.project_id, x.updated_at, NULL, NULL, NULL, NULL, NULL, NULL,
            {rank}
     FROM artifacts x, q
     WHERE (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'message', x.id, c.title, x.content, x.provenance::text, c.project_id,
            x.created_at, NULL, NULL, x.provider, x.conversation_id, NULL, NULL,
            {rank}
     FROM messages x JOIN conversations c ON c.id = x.conversation_id, q
     WHERE $9::bool AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND ($7::text IS NULL OR x.provider = $7)
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT least($8, 8))
""",
            "x.search_tsv",
            "x.id",
            "c.project_id",
        ),
        """
) u
LEFT JOIN projects p ON p.id = u.project_id
""",
    )
)

#: One statement for a project's handoff: newest summaries (tagged, or untagged
#: but naming the project), open tasks and list items, active decisions.
_HANDOFF_SQL = """
WITH pn AS (SELECT to_tsquery('english', NULLIF($2, '')) AS q)
SELECT * FROM (
    (SELECT 'summary'::text AS kind, s.id, s.title, s.summary AS body,
            s.provenance::text AS provenance, s.updated_at AS effective_at,
            s.provider, s.conversation_id, s.open_questions,
            NULL::text AS status, NULL::uuid AS superseded_by,
            (coalesce(s.project_id, c.project_id) IS NULL) AS untagged, 0 AS priority
     FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id, pn
     WHERE coalesce(s.project_id, c.project_id) = $1
        OR (coalesce(s.project_id, c.project_id) IS NULL AND pn.q IS NOT NULL
            AND s.search_tsv @@ pn.q)
     ORDER BY s.updated_at DESC LIMIT $3)
    UNION ALL
    (SELECT 'list_item', li.id, l.name, li.text || coalesce(E'\\n' || li.notes, ''),
            li.provenance::text, li.updated_at, li.source_provider, li.source_conversation_id,
            NULL, NULL, NULL, false, li.priority
     FROM list_items li JOIN lists l ON l.id = li.list_id
     WHERE l.project_id = $1 AND l.status = 'active' AND li.status IN ('open', 'blocked')
     ORDER BY (l.list_type = 'task') DESC, li.priority DESC, li.due_date NULLS LAST,
              li.position LIMIT $4)
    UNION ALL
    (SELECT 'decision', d.id, NULL, d.statement || coalesce(E'\\n\\nWhy: ' || d.rationale, ''),
            d.provenance::text, d.decided_at, d.source_provider, d.source_conversation_id,
            NULL, d.status, NULL, false, 0
     FROM decisions d
     WHERE d.project_id = $1 AND d.status = 'active'
     ORDER BY d.decided_at DESC LIMIT $5)
    UNION ALL
    -- Questions a session of this project marked resolved; hidden from every
    -- summary's open questions below.
    (SELECT 'resolved', s.id, NULL, r.q, NULL, NULL, NULL, NULL, NULL, NULL, NULL, false, 0
     FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id,
          jsonb_array_elements_text(coalesce(s.metadata->'resolved_questions', '[]'::jsonb))
            AS r(q)
     WHERE coalesce(s.project_id, c.project_id) = $1)
) h
"""

#: Newer rows in the same project that share several topic words with a
#: returned decision. Project-name words do not count as shared topic.
_CONFLICT_SQL = """
WITH d AS (
    SELECT x.id, x.project_id, x.decided_at,
           ARRAY(SELECT l FROM unnest(tsvector_to_array(x.search_tsv)) l
                 WHERE l ~ '^[a-z0-9]+$'
                   AND l <> ALL(tsvector_to_array(to_tsvector('english',
                         coalesce(p.name, '') || ' ' || replace(coalesce(p.slug, ''), '-', ' ')))))
             AS lex
    FROM decisions x LEFT JOIN projects p ON p.id = x.project_id
    WHERE x.id = ANY($1::uuid[]) AND x.status = 'active'
)
SELECT d.id AS older_id, d.lex AS older_lex, cand.*
FROM d CROSS JOIN LATERAL (
    SELECT * FROM (
        SELECT 'decision'::text AS kind, o.id, o.statement AS body,
               o.provenance::text AS provenance, o.decided_at AS effective_at,
               tsvector_to_array(o.search_tsv) AS lex
        FROM decisions o
        WHERE o.status = 'active' AND o.id <> d.id AND o.decided_at > d.decided_at
          AND o.project_id IS NOT DISTINCT FROM d.project_id
          AND o.search_tsv @@ to_tsquery('simple', NULLIF(array_to_string(d.lex, ' | '), ''))
        UNION ALL
        SELECT 'summary', s.id, coalesce(s.title || ': ', '') || s.summary, s.provenance::text,
               s.updated_at, tsvector_to_array(s.search_tsv)
        FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
        WHERE s.updated_at > d.decided_at
          AND coalesce(s.project_id, c.project_id) IS NOT DISTINCT FROM d.project_id
          AND s.search_tsv @@ to_tsquery('simple', NULLIF(array_to_string(d.lex, ' | '), ''))
        UNION ALL
        SELECT 'memory', m.id, m.content, m.provenance::text, m.updated_at,
               tsvector_to_array(m.search_tsv)
        FROM memories m
        WHERE m.status = 'active' AND m.updated_at > d.decided_at
          AND m.provenance <> 'imported_metadata'
          AND m.project_id IS NOT DISTINCT FROM d.project_id
          AND m.search_tsv @@ to_tsquery('simple', NULLIF(array_to_string(d.lex, ' | '), ''))
    ) c
    ORDER BY cardinality(ARRAY(SELECT unnest(d.lex) INTERSECT SELECT unnest(c.lex))) DESC,
             (c.provenance = 'user_statement') DESC, (c.kind = 'decision') DESC,
             c.effective_at DESC
    LIMIT 2
) cand
"""


def _name_query(project: inference.ProjectRow | None) -> str:
    """tsquery text matching any way of writing the project's name."""
    if project is None:
        return ""
    phrases = []
    for spelling in project.spellings():
        if len(spelling) == 1 and (len(spelling[0]) < 4 or spelling[0] in inference._GENERIC):
            continue
        phrases.append("(" + " <-> ".join(f"'{w}'" for w in spelling) + ")")
    return " | ".join(phrases)


@dataclass
class _Budget:
    total: int
    spent: int = 0

    def fits(self, cost: int, ceiling: int | None = None) -> bool:
        limit = self.total if ceiling is None else min(self.total, ceiling)
        return self.spent + cost <= limit

    def take(self, cost: int) -> None:
        self.spent += cost


class RetrievalService:
    def __init__(
        self,
        db: Database,
        embeddings: EmbeddingService,
        projects: ProjectService,
        settings: Settings,
    ) -> None:
        self._db = db
        # Kept for the constructor contract. Search and bootstrap deliberately
        # make no embedding call: no model on the read path.
        self._embeddings = embeddings
        self._projects = projects
        self._settings = settings

    # ------------------------------------------------------------ projects

    async def _project_rows(self) -> list[inference.ProjectRow]:
        rows = await self._db.fetch(
            "SELECT id, slug, name, status, metadata FROM projects ORDER BY last_active_at DESC"
        )
        return [inference.ProjectRow.from_record(r) for r in rows]

    # ----------------------------------------------------------- searching

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project: str | None = None,
        provider: str | None = None,
        include_messages: bool = False,
    ) -> list[ContextItem]:
        """Ranked search across every structured layer in one statement."""
        rows = await self._project_rows() if project else []
        match = inference.resolve_reference(project, rows) if project else None
        if project and match is None:
            return []
        return await self._search(
            parse(query),
            limit=limit,
            project=match.project if match else None,
            provider=provider,
            include_messages=include_messages,
        )

    async def _search(
        self,
        parsed: ParsedQuery,
        *,
        limit: int,
        project: inference.ProjectRow | None,
        provider: str | None = None,
        include_messages: bool = False,
        include_unscoped: bool = False,
        exclude: set[UUID] | None = None,
        max_item_tokens: int = _MAX_ITEM_TOKENS,
        flag_conflicts: bool = True,
    ) -> list[ContextItem]:
        if parsed.is_empty:
            return []
        coverage_queries = parsed.coverage_queries()
        rows = await self._db.fetch(
            _SEARCH_SQL,
            parsed.match_query(),
            parsed.rank_query(),
            coverage_queries,
            parsed.phrase_query(),
            list(parsed.ids),
            project.id if project else None,
            provider,
            max(limit * 2, 10),
            include_messages,
            include_unscoped,
            _name_query(project),
        )

        exclude = exclude or set()
        wanted = max(1, len(coverage_queries))
        # With three or more terms, a row must match a fair share of them: one
        # common word ("notes") is not enough to call a row relevant.
        needed = min(3, math.ceil(wanted / 3)) if wanted >= 3 else 1
        scored: list[tuple[float, asyncpg.Record]] = []
        for row in rows:
            if row["id"] in exclude:
                continue
            if row["exact"]:
                text = 100.0
            elif row["coverage"] < needed and not row["phrase_hit"]:
                continue
            else:
                coverage = min(1.0, row["coverage"] / wanted)
                text = (
                    0.15
                    + 0.85 * coverage
                    + 0.1 * min(1.0, row["rank"] * 3)
                    + (0.3 if row["phrase_hit"] else 0.0)
                )
            # Who said it and what kind of item break ties between similar
            # matches; they do not let a weak match outrank a strong one.
            score = (
                text
                * (0.6 + 0.4 * PROVENANCE_WEIGHT.get(row["provenance"], 0.7))
                * (0.7 + 0.3 * KIND_WEIGHT.get(row["kind"], 0.8))
                * _recency(row["effective_at"])
            )
            if row["status"] == "superseded":
                score *= 0.3
            scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        items: list[ContextItem] = []
        seen_text: set[str] = set()
        for score, row in scored:
            normalized = " ".join(row["body"].lower().split())
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            items.append(
                _item(
                    kind=row["kind"],
                    row=row,
                    content=row["body"],
                    title=row["title"],
                    score=score,
                    max_tokens=max_item_tokens,
                )
            )
            if len(items) >= limit:
                break
        if flag_conflicts:
            await self._flag_conflicts(items)
        return items

    # ------------------------------------------------------------ conflicts

    async def _flag_conflicts(
        self, items: list[ContextItem], reports: list[ConflictReport] | None = None
    ) -> None:
        """Mark decisions that a newer item on the same subject may overrule.

        An explicit supersede link is certain. Shared topic words are not, so
        those are flagged as possible conflicts for the user to settle; the
        older decision is never silently preferred or silently dropped.
        """
        by_id = {i.id: i for i in items}
        for item in items:
            if item.status == "superseded" and item.superseded_by:
                report = ConflictReport(
                    kind="superseded",
                    ids=[item.id, item.superseded_by],
                    newer=None if item.superseded_by in by_id else "see superseded_by",
                    newer_provenance="user_statement",
                    advice="Replaced; use the newer decision.",
                )
                if reports is not None and report not in reports:
                    reports.append(report)

        decision_ids = [i.id for i in items if i.type == "decision" and i.status != "superseded"]
        if not decision_ids:
            return
        rows = await self._db.fetch(_CONFLICT_SQL, decision_ids)
        for row in rows:
            older = by_id.get(row["older_id"])
            if older is None:
                continue
            older_lex = set(row["older_lex"] or [])
            shared = older_lex & set(row["lex"] or [])
            if not older_lex or not (
                len(shared) >= 3 or (len(shared) >= 2 and len(shared) / len(older_lex) >= 0.5)
            ):
                continue
            if row["id"] not in older.conflicts_with:
                older.conflicts_with.append(row["id"])
            newer = by_id.get(row["id"])
            if newer is not None and older.id not in newer.conflicts_with:
                newer.conflicts_with.append(older.id)
            if reports is not None:
                reports.append(
                    ConflictReport(
                        kind="possible_conflict",
                        ids=[older.id, row["id"]],
                        newer=None if newer is not None else truncate_for_budget(row["body"], 40),
                        newer_provenance=row["provenance"],
                        advice=f"Newer {row['kind']} on the same subject; ask the user which stands.",
                    )
                )

    # ------------------------------------------------------------- recent

    async def recent(
        self, *, limit: int = 10, provider: str | None = None, project: str | None = None
    ) -> list[ContextItem]:
        project_id = await self._projects.resolve(project) if project else None
        rows = await self._db.fetch(
            """
            SELECT s.id, s.title, s.summary, s.provider AS source_provider,
                   s.conversation_id, s.updated_at, p.slug AS project_slug
            FROM session_summaries s LEFT JOIN projects p ON p.id = s.project_id
            WHERE ($1::text IS NULL OR s.provider = $1)
              AND ($2::uuid IS NULL OR s.project_id = $2)
            ORDER BY s.updated_at DESC LIMIT $3
            """,
            provider,
            project_id,
            limit,
        )
        return [_item(kind="summary", row=r, content=r["summary"], title=r["title"]) for r in rows]

    # ---------------------------------------------------------- packages

    async def for_topic(
        self, topic: str, *, token_budget: int | None = None, project: str | None = None
    ) -> ContextPackage:
        return await self._build_package(
            query=topic,
            token_budget=token_budget or self._settings.default_token_budget,
            project=project,
            include_handoff=False,
        )

    async def bootstrap(
        self,
        query: str | None = None,
        *,
        user_message: str | None = None,
        token_budget: int | None = None,
        project: str | None = None,
    ) -> ContextPackage:
        """The package that starts a chat: handoff, then relevant items, then raw.

        ``query`` is a few key terms chosen by the calling model. A whole
        ``user_message`` (the older calling convention) still works: it is
        reduced to its key terms here rather than searched as one block.
        """
        text = query if query and query.strip() else (user_message or "")
        notes: list[str] = []
        if user_message and not (query and query.strip()):
            notes.append(
                "user_message is deprecated; pass query (key terms). Reduced to key terms."
            )
        return await self._build_package(
            query=text,
            token_budget=token_budget or self._settings.default_token_budget,
            project=project,
            include_handoff=True,
            notes=notes,
        )

    async def _build_package(
        self,
        *,
        query: str,
        token_budget: int,
        project: str | None,
        include_handoff: bool,
        notes: list[str] | None = None,
    ) -> ContextPackage:
        notes = list(notes or [])
        parsed = parse(query)
        budget = _Budget(token_budget, spent=_PACKAGE_OVERHEAD)
        item_cap = max(60, min(_MAX_ITEM_TOKENS, token_budget // 4))
        chosen: list[ContextItem] = []
        seen: set[UUID] = set()
        layers: list[LayerReport] = []
        conflicts: list[ConflictReport] = []
        left_out = 0

        tiers: dict[UUID, int] = {}
        compact = token_budget < _COMPACT_BELOW

        def take(items: list[ContextItem], layer: str, ceiling: int | None = None) -> int:
            """Add what fits; skip what does not and keep going. Returns count added."""
            nonlocal left_out
            added = 0
            for position, item in enumerate(items):
                if item.id in seen:
                    continue
                # The package names the project once; a conversation id is kept
                # where it leads somewhere (a session's transcript).
                if focus is not None and item.project == focus.slug:
                    item.project = None
                if compact or item.type != "summary":
                    item.provenance.conversation_id = None
                item.estimated_tokens = _cost(item)
                if not budget.fits(item.estimated_tokens, ceiling):
                    left_out += 1
                    continue
                item.layer = layer
                if layer == "raw":
                    tiers[item.id] = _TIER_RAW
                elif layer == "handoff":
                    tiers[item.id] = _TIER_CORE if position < 7 else _TIER_HANDOFF_REST
                else:
                    tiers[item.id] = _TIER_RELEVANT_TOP if position < 3 else _TIER_RELEVANT_REST
                chosen.append(item)
                seen.add(item.id)
                budget.take(item.estimated_tokens)
                added += 1
            return added

        # -- which project ------------------------------------------------
        rows = await self._project_rows()
        choice, focus, candidates = self._choose_project(project, parsed.text, rows)

        # -- 1. handoff ---------------------------------------------------
        handoff_items = 0
        if not include_handoff:
            layers.append(
                LayerReport(name="handoff", status="skipped", reason="topic packages skip it")
            )
        elif focus is None and not candidates:
            layers.append(LayerReport(name="handoff", status="skipped", reason="no project"))
        else:
            targets = [focus] if focus else candidates[:2]
            short = focus is None
            share = 1.0 if parsed.is_empty else 0.7
            for target in targets:
                ceiling = budget.spent + int(token_budget * share / len(targets))
                items = await self._handoff(target, short=short, item_cap=item_cap, parsed=parsed)
                handoff_items += take(items, "handoff", ceiling)
            layers.append(
                LayerReport(
                    name="handoff",
                    status="ran",
                    reason=None if focus else "short handoff per candidate project",
                    items=handoff_items,
                )
            )

        # -- 2. relevant --------------------------------------------------
        relevant = 0
        if parsed.is_empty:
            layers.append(
                LayerReport(
                    name="relevant",
                    status="skipped",
                    reason="no query terms",
                )
            )
        else:
            found = await self._search(
                parsed,
                limit=12,
                project=focus,
                include_unscoped=focus is not None,
                exclude=seen,
                max_item_tokens=item_cap,
                flag_conflicts=False,
            )
            relevant = take(found, "relevant")
            layers.append(
                LayerReport(
                    name="relevant",
                    status="ran",
                    items=relevant,
                )
            )

        # -- 3. raw transcripts --------------------------------------------
        if handoff_items or relevant:
            layers.append(
                LayerReport(
                    name="raw_messages",
                    status="skipped",
                    reason="not needed",
                )
            )
        elif parsed.is_empty:
            layers.append(
                LayerReport(name="raw_messages", status="skipped", reason="no query terms")
            )
        else:
            raw = await self._search(
                parsed,
                limit=4,
                project=focus,
                include_messages=True,
                include_unscoped=focus is not None,
                exclude=seen,
                max_item_tokens=item_cap,
                flag_conflicts=False,
            )
            raw = [i for i in raw if i.type == "message"]
            added = take(raw, "raw")
            if added:
                notes.append(
                    "Included raw message excerpts because nothing structured matched. "
                    "These are verbatim transcript fragments."
                )
            layers.append(
                LayerReport(
                    name="raw_messages",
                    status="ran",
                    items=added,
                )
            )

        await self._flag_conflicts(chosen, conflicts)

        if left_out:
            notes.append(f"{left_out} more items did not fit token_budget.")
        if not chosen:
            notes.append(_NOTHING_MATCHED)

        layers_used = []
        for item in chosen:
            name = {"handoff": "handoff", "relevant": "relevant", "raw": "raw_messages"}[
                item.layer or "relevant"
            ]
            if name not in layers_used:
                layers_used.append(name)

        for layer_report in layers:
            layer_report.items = layer_report.items or None
        if compact:
            # layers_used already names the layers that returned items.
            layers = [r for r in layers if r.status == "skipped" or not r.items]
        package = ContextPackage(
            query=truncate_for_budget(query, 60),
            items=chosen,
            token_budget=token_budget,
            estimated_tokens=0,
            layers_used=layers_used,
            truncated=left_out > 0,
            notes=notes,
            keywords=parsed.keywords,
            project=choice,
            layers=layers,
            conflicts=conflicts,
        )
        # The budget covers the whole result as sent, not just item text.
        # Measure it, and drop from the end (least important first) until it fits.
        # A conflict pair is only useful together: both sides get the more
        # important tier of the two.
        for report in conflicts:
            pair = [i for i in report.ids if i in tiers]
            if len(pair) == 2:
                tier = min(tiers[pair[0]], tiers[pair[1]])
                tiers[pair[0]] = tiers[pair[1]] = tier
        size = _wire_tokens(package)
        while size > token_budget and package.items:
            # Least important tier first; within a tier, the last one added.
            worst = max(
                range(len(package.items)),
                key=lambda i: (tiers.get(package.items[i].id, _TIER_RAW), i),
            )
            dropped = package.items.pop(worst)
            kept: list[ConflictReport] = []
            for report in package.conflicts:
                if report.ids[0] == dropped.id:
                    continue  # the older item is gone, so is the warning about it
                if report.ids[-1] == dropped.id and report.newer is None:
                    report.newer = truncate_for_budget(dropped.content, 40)
                kept.append(report)
            package.conflicts = kept
            package.truncated = True
            size = _wire_tokens(package)
        package.estimated_tokens = size
        return package

    def _choose_project(
        self, project: str | None, text: str, rows: list[inference.ProjectRow]
    ) -> tuple[ProjectChoice, inference.ProjectRow | None, list[inference.ProjectRow]]:
        if project and project.strip():
            match = inference.resolve_reference(project, rows)
            if match:
                return (
                    ProjectChoice(
                        slug=match.project.slug,
                        name=match.project.name,
                        source="given",
                        reason=match.reason,
                    ),
                    match.project,
                    [],
                )
            guess = inference.infer(project, rows)
            return (
                ProjectChoice(
                    source="none",
                    reason=f"no project matches {project!r}",
                    candidates=[
                        ProjectCandidate(slug=m.project.slug, name=m.project.name, reason=m.reason)
                        for m in guess.candidates[:2]
                    ],
                ),
                None,
                [],
            )
        found = inference.infer(text, rows)
        if found.project:
            return (
                ProjectChoice(
                    slug=found.project.slug,
                    name=found.project.name,
                    source="inferred",
                    reason=found.reason,
                ),
                found.project,
                [],
            )
        return (
            ProjectChoice(
                source="none",
                reason=found.reason,
                candidates=[
                    ProjectCandidate(slug=m.project.slug, name=m.project.name, reason=m.reason)
                    for m in found.candidates[:2]
                ],
            ),
            None,
            # Short handoffs only when the query really could mean either
            # project; a single weak hint is reported, not acted on.
            [m.project for m in found.candidates[:2]] if found.tied else [],
        )

    async def _handoff(
        self,
        project: inference.ProjectRow,
        *,
        short: bool,
        item_cap: int,
        parsed: ParsedQuery | None = None,
    ) -> list[ContextItem]:
        """Newest summaries, open tasks and active decisions, in reading order."""
        rows = await self._db.fetch(
            _HANDOFF_SQL,
            project.id,
            _name_query(project),
            1 if short else 3,
            2 if short else 8,
            2 if short else 8,
        )
        summaries: list[ContextItem] = []
        tasks: list[ContextItem] = []
        decisions: list[ContextItem] = []
        resolved = {question_key(r["body"]) for r in rows if r["kind"] == "resolved"}
        for row in rows:
            if row["kind"] == "resolved":
                continue
            data = dict(row)
            if row["kind"] == "summary" and resolved:
                data["open_questions"] = [
                    q for q in row["open_questions"] or [] if question_key(q) not in resolved
                ]
            data["project_slug"] = project.slug
            if row["kind"] == "summary" and row["untagged"]:
                data["reason"] = f"untagged; mentions {project.name}"
            item = _item(
                kind=row["kind"],
                row=data,
                content=row["body"],
                title=row["title"],
                max_tokens=item_cap,
            )
            {"summary": summaries, "list_item": tasks, "decision": decisions}[row["kind"]].append(
                item
            )
        summaries.sort(key=lambda i: i.effective_at or _now(), reverse=True)
        if parsed is not None and parsed.terms:
            # Within tasks and decisions, the ones that mention the query lead.
            stems = [t[:5] for t in parsed.terms]

            def mentions(item: ContextItem) -> bool:
                text = item.content.lower()
                return any(stem in text for stem in stems)

            tasks.sort(key=lambda i: not mentions(i))
            decisions.sort(key=lambda i: not mentions(i))
        # Newest session first; then what is open; then what was decided. Older
        # sessions come after, so a tight budget still gets the latest of each.
        ordered = summaries[:1] + tasks[:3] + decisions[:3] + summaries[1:] + tasks[3:]
        return ordered + decisions[3:]
