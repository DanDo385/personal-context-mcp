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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
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
#: Conflict reports per package; the rest are dropped, newest-statement first kept.
_MAX_CONFLICT_REPORTS = 3

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
#: returned decision. Project-name words do not count as shared topic, and
#: neither do words found in at least 30% of the decisions and sessions of a
#: project with 20 or more (the user's own name, the product's jargon): those say nothing
#: about whether two items are about the same thing.
_CONFLICT_SQL = """
WITH x AS (
    SELECT id, project_id, decided_at, search_tsv FROM decisions
    WHERE id = ANY($1::uuid[]) AND status = 'active'
), docs AS (
    SELECT o.project_id, o.search_tsv FROM decisions o
    WHERE o.status = 'active' AND o.project_id IN (SELECT project_id FROM x)
    UNION ALL
    SELECT coalesce(s.project_id, c.project_id), s.search_tsv
    FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
    WHERE coalesce(s.project_id, c.project_id) IN (SELECT project_id FROM x)
), sizes AS (
    SELECT project_id, count(*) AS n FROM docs GROUP BY project_id
), common AS (
    SELECT docs.project_id, l
    FROM docs JOIN sizes USING (project_id), unnest(tsvector_to_array(docs.search_tsv)) l
    WHERE sizes.n >= 20
    GROUP BY docs.project_id, sizes.n, l
    HAVING count(*) >= 0.3 * sizes.n
), d AS (
    SELECT x.id, x.project_id, x.decided_at,
           ARRAY(SELECT l FROM unnest(tsvector_to_array(x.search_tsv)) l
                 WHERE l ~ '^[a-z0-9]+$'
                   AND l <> ALL(tsvector_to_array(to_tsvector('english',
                         coalesce(p.name, '') || ' ' || replace(coalesce(p.slug, ''), '-', ' '))))
                   AND l NOT IN (SELECT common.l FROM common
                                 WHERE common.project_id = x.project_id))
             AS lex
    FROM x LEFT JOIN projects p ON p.id = x.project_id
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
class _Conflict:
    older: UUID
    newer: UUID
    kind: Literal["superseded", "possible_conflict"]
    newer_kind: str
    newer_provenance: str
    newer_body: str | None


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
            self._mark_conflicts(items, await self._find_conflicts(items))
        return items

    # ------------------------------------------------------------ conflicts

    async def _find_conflicts(self, items: list[ContextItem]) -> list[_Conflict]:
        """Pairs where a newer item may overrule a returned decision. One query.

        An explicit supersede link is certain. Shared topic words are not, so
        those are possible conflicts for the user to settle; the older decision
        is never silently preferred or silently dropped.
        """
        found: list[_Conflict] = [
            _Conflict(
                older=i.id,
                newer=i.superseded_by,
                kind="superseded",
                newer_kind="decision",
                newer_provenance="user_statement",
                newer_body=None,
            )
            for i in items
            if i.status == "superseded" and i.superseded_by
        ]
        decision_ids = [i.id for i in items if i.type == "decision" and i.status != "superseded"]
        if not decision_ids:
            return found
        for row in await self._db.fetch(_CONFLICT_SQL, decision_ids):
            older_lex = set(row["older_lex"] or [])
            shared = older_lex & set(row["lex"] or [])
            if not older_lex or not (
                len(shared) >= 3 or (len(shared) >= 2 and len(shared) / len(older_lex) >= 0.5)
            ):
                continue
            found.append(
                _Conflict(
                    older=row["older_id"],
                    newer=row["id"],
                    kind="possible_conflict",
                    newer_kind=row["kind"],
                    newer_provenance=row["provenance"],
                    newer_body=row["body"],
                )
            )
        return found

    @staticmethod
    def _mark_conflicts(items: list[ContextItem], conflicts: list[_Conflict]) -> None:
        """Point each item at the other side of its conflicts, when both are present."""
        by_id = {i.id: i for i in items}
        for conflict in conflicts:
            if conflict.kind == "superseded":
                continue  # carried by status and superseded_by
            older, newer = by_id.get(conflict.older), by_id.get(conflict.newer)
            if older is not None and conflict.newer not in older.conflicts_with:
                older.conflicts_with.append(conflict.newer)
            if older is not None and newer is not None and older.id not in newer.conflicts_with:
                newer.conflicts_with.append(older.id)

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
        """Gather candidates from every layer, then fill the budget by priority.

        Priority: the newest session, the two best matches for the query (each
        with the newer item that may overrule it), two open tasks, two
        decisions, older sessions, then everything else. Items are then shown
        in reading order: handoff, relevant, raw.
        """
        notes = list(notes or [])
        parsed = parse(query)
        compact = token_budget < _COMPACT_BELOW
        item_cap = max(60, min(_MAX_ITEM_TOKENS, token_budget // 6))
        layers: list[LayerReport] = []

        # -- which project ------------------------------------------------
        rows = await self._project_rows()
        choice, focus, candidates = self._choose_project(project, parsed.text, rows)

        # -- 1. handoff candidates ---------------------------------------
        handoffs: list[list[ContextItem]] = []
        if not include_handoff:
            layers.append(LayerReport(name="handoff", status="skipped", reason="topic package"))
        elif focus is None and not candidates:
            layers.append(LayerReport(name="handoff", status="skipped", reason="no project"))
        else:
            for target in [focus] if focus else candidates[:2]:
                handoffs.append(
                    await self._handoff(
                        target, short=focus is None, item_cap=item_cap, parsed=parsed
                    )
                )
        handoff_ids = {i.id for h in handoffs for i in h}

        # -- 2. relevant candidates --------------------------------------
        # Once the project is known, its own name is not a search term: it
        # matches nearly everything the project holds.
        if focus is not None:
            name_words = {w for spelling in focus.spellings() for w in spelling}
            searched = replace(
                parsed,
                terms=tuple(t for t in parsed.terms if t not in name_words),
                compounds=tuple(c for c in parsed.compounds if not set(c) <= name_words),
            )
        else:
            searched = parsed
        relevant: list[ContextItem] = []
        if not searched.is_empty:
            relevant = await self._search(
                searched,
                limit=12,
                project=focus,
                include_unscoped=focus is not None,
                exclude=handoff_ids,
                max_item_tokens=item_cap,
                flag_conflicts=False,
            )

        # -- 3. raw candidates, only when nothing else matched -----------
        raw: list[ContextItem] = []
        if not handoff_ids and not relevant and not searched.is_empty:
            found = await self._search(
                searched,
                limit=4,
                project=focus,
                include_messages=True,
                include_unscoped=focus is not None,
                max_item_tokens=item_cap,
                flag_conflicts=False,
            )
            raw = [i for i in found if i.type == "message"]

        for layer, group in (
            ("handoff", [i for h in handoffs for i in h]),
            ("relevant", relevant),
            ("raw", raw),
        ):
            for item in group:
                item.layer = layer
                if focus is not None and item.project == focus.slug:
                    item.project = None  # the package names it once
                if compact or item.type != "summary":
                    item.provenance.conversation_id = None
                item.estimated_tokens = _cost(item)

        everything = {i.id: i for h in handoffs for i in h} | {i.id: i for i in relevant + raw}
        conflicts = await self._find_conflicts(list(everything.values()))
        partners: dict[UUID, list[UUID]] = {}
        for c in conflicts:
            partners.setdefault(c.older, []).append(c.newer)
            partners.setdefault(c.newer, []).append(c.older)

        # -- fill by priority ----------------------------------------------
        order: list[ContextItem] = []
        for h in handoffs:
            order += h[:1]  # newest session of each project
        order += relevant[:2]
        for h in handoffs:
            order += h[1:5]  # two open tasks, two decisions
        for h in handoffs:
            order += h[5:]
        order += relevant[2:] + raw

        budget = _Budget(token_budget, spent=_PACKAGE_OVERHEAD)
        chosen: dict[UUID, ContextItem] = {}
        left_out = 0

        def add(item: ContextItem) -> bool:
            if item.id in chosen:
                return True
            if not budget.fits(item.estimated_tokens):
                return False
            chosen[item.id] = item
            budget.take(item.estimated_tokens)
            return True

        for item in order:
            if not add(item):
                left_out += 1
                continue
            # The newer side of a conflict travels with the decision it may overrule.
            for other in partners.get(item.id, []):
                if other in everything:
                    add(everything[other])

        # -- conflict reports for what was chosen ----------------------------
        reports: list[ConflictReport] = []
        for c in sorted(
            conflicts,
            key=lambda c: (
                c.kind != "superseded",
                not (c.older in chosen and c.newer in chosen),
                c.newer_provenance != "user_statement",
            ),
        ):
            if c.older not in chosen and c.newer not in chosen:
                continue
            if len(reports) == _MAX_CONFLICT_REPORTS:
                break
            older_text = None
            if c.older not in chosen and c.older in everything:
                older_text = truncate_for_budget(everything[c.older].content, 40)
            newer_text = None
            if c.newer not in chosen:
                body = everything[c.newer].content if c.newer in everything else c.newer_body
                newer_text = truncate_for_budget(body, 40) if body else "see superseded_by"
            reports.append(
                ConflictReport(
                    kind=c.kind,
                    ids=[c.older, c.newer],
                    older=older_text,
                    newer=newer_text,
                    newer_provenance=c.newer_provenance,
                    advice=(
                        "Replaced; use the newer decision."
                        if c.kind == "superseded"
                        else f"Newer {c.newer_kind} on the same subject; ask the user which stands."
                    ),
                )
            )
        shown = [i for i in order if i.id in chosen]
        self._mark_conflicts(
            shown,
            [c for c in conflicts if any(r.ids == [c.older, c.newer] for r in reports)],
        )

        # -- reading order and layer report ----------------------------------
        rank = {"handoff": 0, "relevant": 1, "raw": 2}
        position = {
            i.id: n for n, i in enumerate([*(i for h in handoffs for i in h), *relevant, *raw])
        }
        items = sorted(shown, key=lambda i: (rank[i.layer or "relevant"], position[i.id]))
        counts = {name: sum(1 for i in items if i.layer == name) for name in rank}

        if handoffs:
            layers.append(
                LayerReport(
                    name="handoff",
                    status="ran",
                    reason=None if focus else "short handoff per candidate project",
                    items=counts["handoff"] or None,
                )
            )
        if searched.is_empty:
            layers.append(
                LayerReport(
                    name="relevant",
                    status="skipped",
                    reason="no query terms" if parsed.is_empty else "query only names the project",
                )
            )
        else:
            layers.append(
                LayerReport(name="relevant", status="ran", items=counts["relevant"] or None)
            )
        if raw or (not handoff_ids and not relevant and not searched.is_empty):
            layers.append(
                LayerReport(name="raw_messages", status="ran", items=counts["raw"] or None)
            )
            if counts["raw"]:
                notes.append(
                    "Included raw message excerpts because nothing structured matched. "
                    "These are verbatim transcript fragments."
                )
        else:
            layers.append(
                LayerReport(
                    name="raw_messages",
                    status="skipped",
                    reason="not needed" if (handoff_ids or relevant) else "no query terms",
                )
            )
        if compact:
            # layers_used already names the layers that returned items.
            layers = [r for r in layers if r.status == "skipped" or not r.items]

        if left_out:
            notes.append(f"{left_out} more items did not fit token_budget.")
        if not items:
            notes.append(_NOTHING_MATCHED)

        package = ContextPackage(
            query=truncate_for_budget(query, 60),
            items=items,
            token_budget=token_budget,
            estimated_tokens=0,
            layers_used=[{"raw": "raw_messages"}.get(name, name) for name in rank if counts[name]],
            truncated=left_out > 0,
            notes=notes,
            keywords=parsed.keywords,
            project=choice,
            layers=layers,
            conflicts=reports,
        )
        # The budget covers the whole result as sent. If the estimate above was
        # short, drop the lowest-priority items until it fits.
        size = _wire_tokens(package)
        priority = {i.id: n for n, i in enumerate(order)}
        while size > token_budget and package.items:
            worst = max(package.items, key=lambda i: priority.get(i.id, len(order)))
            package.items.remove(worst)
            for report in package.conflicts:
                if report.ids[0] == worst.id and report.older is None:
                    report.older = truncate_for_budget(worst.content, 40)
                if report.ids[-1] == worst.id and report.newer is None:
                    report.newer = truncate_for_budget(worst.content, 40)
            for kept_item in package.items:
                if worst.id in kept_item.conflicts_with:
                    kept_item.conflicts_with.remove(worst.id)
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
        ordered = summaries[:1] + tasks[:2] + decisions[:2] + summaries[1:] + tasks[2:]
        return ordered + decisions[2:]
