"""The wiki librarian: links between notes, and Maps of Content.

The division of labour follows the rest of the classifier design:

* **Python finds candidates.** For each note, a handful of plausible targets
  from full-text overlap, a shared project and (when embeddings exist) vector
  similarity. Never every pair: 10,000 notes would be 100 million questions.
* **Code answers what code can.** A note that names another note's title
  links to it. No model is asked.
* **Jev judges the rest.** Should this link exist, is it worth navigating,
  and what kind of relation is it; which Map of Content a note belongs in and
  which section. Typed answers with probabilities, never prose.
* **Python applies thresholds** from ``classifier/questions.py``: act, send to
  review, or record the rejection so the pair is not asked again.
* **Postgres stores the graph** (``note_edges``, ``mocs``,
  ``moc_memberships``). The vault receives a rendering of it - a generated
  block at the foot of each note and one generated note per map - produced
  when the worker leases the job, so relinking never touches what a person
  wrote.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from personal_context_mcp.classifier.questions import (
    MOC_AUTO,
    MOC_REVIEW,
    NO_MOC,
    QUESTIONS_VERSION,
    WIKI_LINK_AUTO,
    WIKI_LINK_NAVIGATION_AUTO,
    WIKI_LINK_REVIEW,
)
from personal_context_mcp.importers.sensitivity import raise_to
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.services import Services
from personal_context_mcp.util import slugify, truncate_for_budget

logger = get_logger(__name__)

#: Rendered order and headings of MOC sections (keys: MOC_SECTION_CRITERIA).
SECTION_TITLES: dict[str, str] = {
    "fundamentals": "Fundamentals",
    "how_to": "How-to",
    "implementations": "Implementations",
    "research": "Research",
    "decisions": "Decisions",
    "reference": "Reference",
    "other": "Other",
}

#: Titles shorter than this are too generic to link on a bare mention.
_MIN_MENTION_TITLE = 4
_EXCERPT_CHARS = 1500


@dataclass
class WikiReport:
    notes_scanned: int = 0
    pairs_considered: int = 0
    title_links: int = 0
    asked: int = 0
    cache_hits: int = 0
    accepted: int = 0
    review: int = 0
    rejected: int = 0
    degraded: int = 0
    mocs_created: int = 0
    memberships_accepted: int = 0
    memberships_review: int = 0
    notes_queued: int = 0
    mocs_queued: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "errors" and v}
        if self.errors:
            data["errors"] = self.errors[:50]
        return data


def stem(vault_path: str) -> str:
    return PurePosixPath(vault_path).stem


class WikiService:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._db = services.db
        self._classification = services.classification
        self._settings = services.settings

    # ------------------------------------------------------------ links

    async def _title_index(self) -> tuple[re.Pattern[str] | None, dict[str, UUID]]:
        rows = await self._db.fetch(
            "SELECT id, title FROM notes WHERE vault_path IS NOT NULL AND length(title) >= $1",
            _MIN_MENTION_TITLE,
        )
        by_title = {row["title"].lower(): row["id"] for row in rows}
        if not by_title:
            return None, {}
        # One alternation, longest first, so "MCP OAuth" wins over "MCP".
        alternation = "|".join(re.escape(t) for t in sorted(by_title, key=len, reverse=True))
        return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.I), by_title

    async def _decided(self, note_id: UUID) -> tuple[set[UUID], set[UUID]]:
        """(targets decided from this note, notes decided in either direction).

        A title mention yields only to a decision in its own direction. Jev is
        not asked about a pair decided either way: an accepted link already
        gives Obsidian a backlink, and relevance is close enough to symmetric
        that a rejection is not worth paying to ask again in reverse.
        """
        rows = await self._db.fetch(
            """
            SELECT target_note_id AS other, true AS outgoing
            FROM note_edges WHERE source_note_id = $1
            UNION ALL
            SELECT source_note_id, false FROM note_edges WHERE target_note_id = $1
            """,
            note_id,
        )
        outgoing = {row["other"] for row in rows if row["outgoing"]}
        return outgoing, {row["other"] for row in rows}

    async def _candidates(self, note: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            WITH q AS (
                SELECT to_tsquery('english', coalesce(nullif(array_to_string(
                    tsvector_to_array(to_tsvector('english', $2)), ' | '), ''), 'zzzznomatch')) AS q
            ),
            by_text AS (
                SELECT n.id, ts_rank(n.search_tsv, q.q) AS score
                FROM notes n, q
                WHERE n.id <> $1 AND n.vault_path IS NOT NULL AND n.search_tsv @@ q.q
                ORDER BY score DESC LIMIT $3
            ),
            by_project AS (
                SELECT n.id, 0.05::real AS score FROM notes n
                WHERE $4::uuid IS NOT NULL AND n.project_id = $4 AND n.id <> $1
                  AND n.vault_path IS NOT NULL
                ORDER BY n.updated_at DESC LIMIT $3
            ),
            by_vector AS (
                SELECT n.id, (1 - (n.embedding <=> s.embedding))::real AS score
                FROM notes n, notes s
                WHERE s.id = $1 AND s.embedding IS NOT NULL AND n.embedding IS NOT NULL
                  AND n.id <> $1 AND n.vault_path IS NOT NULL
                ORDER BY n.embedding <=> s.embedding LIMIT $3
            ),
            merged AS (
                SELECT id, max(score) AS score FROM (
                    SELECT * FROM by_text UNION ALL SELECT * FROM by_project
                    UNION ALL SELECT * FROM by_vector
                ) c GROUP BY id
            )
            SELECT n.id, n.title, left(n.content, $5) AS excerpt, n.sensitivity::text AS sensitivity
            FROM merged JOIN notes n USING (id)
            ORDER BY merged.score DESC LIMIT $3
            """,
            note["id"],
            # Title plus the opening of the body: the title alone misses too
            # much, the whole body matches everything.
            f"{note['title']} {note['content'][:400]}",
            limit,
            note["project_id"],
            _EXCERPT_CHARS,
        )
        return [dict(row) for row in rows]

    async def _insert_edge(
        self,
        source_id: UUID,
        target_id: UUID,
        *,
        relation: str,
        confidence: float,
        status: str,
        source: str,
        event_id: UUID | None = None,
    ) -> bool:
        inserted = await self._db.fetchval(
            """
            INSERT INTO note_edges (
                source_note_id, target_note_id, relation_type, confidence, status,
                source, classification_event_id, questions_version
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (source_note_id, target_note_id) DO UPDATE
                SET relation_type = EXCLUDED.relation_type,
                    confidence = EXCLUDED.confidence,
                    status = EXCLUDED.status,
                    source = EXCLUDED.source,
                    classification_event_id = EXCLUDED.classification_event_id,
                    questions_version = EXCLUDED.questions_version,
                    updated_at = now()
                -- A person's decision is never overwritten by a classifier.
                WHERE note_edges.source <> 'human'
            RETURNING id
            """,
            source_id,
            target_id,
            relation,
            confidence,
            status,
            source,
            event_id,
            QUESTIONS_VERSION,
        )
        return inserted is not None

    async def _judge_pair(
        self, note: dict[str, Any], target: dict[str, Any], report: WikiReport
    ) -> None:
        state = {
            "source_title": note["title"],
            "source_excerpt": truncate_for_budget(note["content"], _EXCERPT_CHARS),
            "target_title": target["title"],
            "target_excerpt": target["excerpt"],
            # The stricter of the two decides whether this may leave the process.
            "sensitivity": raise_to(note["sensitivity"], target["sensitivity"]),
        }
        result, extra = await self._classification.classify_note_link(state)
        report.asked += 1
        report.cache_hits += 1 if extra.get("cache_hit") else 0
        if result.degraded:
            report.degraded += 1
            return

        should = result.noul("should_link", 0.0)
        navigation = result.noul("navigation_value", 0.0)
        relation, _ = result.choice("relation")
        if should >= WIKI_LINK_AUTO and navigation >= WIKI_LINK_NAVIGATION_AUTO:
            status = "accepted"
        elif should >= WIKI_LINK_REVIEW:
            status = "review"
        else:
            status = "rejected"

        event_id = await self._classification.record_raw(
            result,
            object_type="note_link",
            object_id=note["id"],
            action_taken=status,
            confidence=should,
            metadata={"target_note_id": str(target["id"]), **extra},
        )
        await self._insert_edge(
            note["id"],
            target["id"],
            relation=relation or "related_concept",
            confidence=should,
            status=status,
            source="jev",
            event_id=event_id,
        )
        if status == "accepted":
            report.accepted += 1
        elif status == "review":
            report.review += 1
            await self._classification.queue_review(
                object_type="note_link",
                object_id=note["id"],
                reason=f"link:{target['id']}",
                question=f"Link {note['title']!r} to {target['title']!r} ({relation})?",
                suggested={"target_note_id": str(target["id"]), "relation": relation},
                confidence=should,
                classification_event_id=event_id,
            )
        else:
            report.rejected += 1

    async def build_links(
        self, *, limit: int = 200, per_note: int = 12, redo: bool = False
    ) -> WikiReport:
        report = WikiReport()
        pattern, by_title = await self._title_index()
        notes = await self._db.fetch(
            """
            SELECT id, title, content, sensitivity::text AS sensitivity, project_id
            FROM notes
            WHERE vault_path IS NOT NULL
              AND ($2 OR NOT EXISTS (SELECT 1 FROM note_edges e WHERE e.source_note_id = notes.id))
            ORDER BY updated_at DESC
            LIMIT $1
            """,
            limit,
            redo,
        )
        semaphore = asyncio.Semaphore(max(1, self._settings.import_concurrency))

        async def judge(note: dict[str, Any], target: dict[str, Any]) -> None:
            async with semaphore:
                try:
                    await self._judge_pair(note, target, report)
                except Exception as exc:
                    logger.exception("link classification failed")
                    report.errors.append(f"{note['title']} -> {target['title']}: {exc}")

        for row in notes:
            note = dict(row)
            report.notes_scanned += 1
            outgoing, decided = (set(), set()) if redo else await self._decided(note["id"])

            # Deterministic: the note names another note.
            if pattern is not None:
                for match in {m.group(0).lower() for m in pattern.finditer(note["content"])}:
                    target_id = by_title.get(match)
                    if target_id and target_id != note["id"] and target_id not in outgoing:
                        await self._insert_edge(
                            note["id"],
                            target_id,
                            relation="related_concept",
                            confidence=1.0,
                            status="accepted",
                            source="title_mention",
                        )
                        decided.add(target_id)
                        report.title_links += 1

            targets = [t for t in await self._candidates(note, per_note) if t["id"] not in decided]
            report.pairs_considered += len(targets)
            await asyncio.gather(*(judge(note, target) for target in targets))
        return report

    # ------------------------------------------------------------- MOCs

    async def add_moc(
        self, title: str, *, description: str | None = None, parent: str | None = None
    ) -> UUID:
        parent_id = (
            await self._db.fetchval("SELECT id FROM mocs WHERE slug = $1", slugify(parent))
            if parent
            else None
        )
        return await self._db.fetchval(
            """
            INSERT INTO mocs (slug, title, description, parent_moc_id, source)
            VALUES ($1, $2, $3, $4, 'human')
            ON CONFLICT (slug) DO UPDATE
                SET description = coalesce(EXCLUDED.description, mocs.description),
                    parent_moc_id = coalesce(EXCLUDED.parent_moc_id, mocs.parent_moc_id),
                    updated_at = now()
            RETURNING id
            """,
            slugify(title),
            title.strip(),
            description,
            parent_id,
        )

    async def seed_mocs(self, *, min_mentions: int = 3) -> list[str]:
        """Propose a map for every topic that deep extraction named in at
        least ``min_mentions`` conversations. Deterministic; nothing is
        assigned to the new maps until ``assign_mocs`` runs."""
        rows = await self._db.fetch(
            """
            SELECT min(t) AS title, count(DISTINCT c.id) AS mentions
            FROM conversations c,
                 jsonb_array_elements_text(coalesce(c.metadata->'extraction'->'topics', '[]')) t
            GROUP BY lower(t)
            HAVING count(DISTINCT c.id) >= $1
            ORDER BY count(DISTINCT c.id) DESC
            """,
            min_mentions,
        )
        created: list[str] = []
        for row in rows:
            slug = slugify(row["title"])
            if not slug:
                continue
            inserted = await self._db.fetchval(
                """
                INSERT INTO mocs (slug, title, source) VALUES ($1, $2, 'topics')
                ON CONFLICT (slug) DO NOTHING RETURNING id
                """,
                slug,
                row["title"],
            )
            if inserted:
                created.append(row["title"])
        return created

    async def _moc_candidates(self, note: dict[str, Any], cap: int) -> dict[str, str]:
        total = await self._db.fetchval("SELECT count(*) FROM mocs")
        if total <= cap:
            rows = await self._db.fetch("SELECT slug, title, description FROM mocs")
        else:
            rows = await self._db.fetch(
                """
                WITH q AS (
                    SELECT to_tsquery('english', coalesce(nullif(array_to_string(
                        tsvector_to_array(to_tsvector('english', $1)), ' | '), ''),
                        'zzzznomatch')) AS q
                )
                SELECT slug, title, description FROM mocs, q
                WHERE search_tsv @@ q.q
                ORDER BY ts_rank(search_tsv, q.q) DESC LIMIT $2
                """,
                f"{note['title']} {note['content'][:600]}",
                cap,
            )
        return {
            row["slug"]: truncate_for_budget(
                f"{row['title']}. {row['description'] or ''}".strip(" ."), 300
            )
            for row in rows
        }

    async def assign_mocs(self, *, limit: int = 200, redo: bool = False) -> WikiReport:
        report = WikiReport()
        notes = await self._db.fetch(
            """
            SELECT id, title, content, sensitivity::text AS sensitivity
            FROM notes
            WHERE vault_path IS NOT NULL
              AND ($2 OR NOT EXISTS (
                    SELECT 1 FROM moc_memberships mm WHERE mm.note_id = notes.id))
            ORDER BY updated_at DESC
            LIMIT $1
            """,
            limit,
            redo,
        )
        for row in notes:
            note = dict(row)
            report.notes_scanned += 1
            candidates = await self._moc_candidates(note, self._settings.jev_max_project_candidates)
            if not candidates:
                continue
            state = {
                "title": note["title"],
                "excerpt": truncate_for_budget(note["content"], _EXCERPT_CHARS),
                "sensitivity": note["sensitivity"],
            }
            try:
                result, extra = await self._classification.classify_moc(state, candidates)
            except Exception as exc:
                report.errors.append(f"{note['title']}: {exc}")
                continue
            report.asked += 1
            report.cache_hits += 1 if extra.get("cache_hit") else 0
            if result.degraded:
                report.degraded += 1
                continue

            slug, confidence = result.choice("moc")
            section, _ = result.choice("section")
            if not slug or slug == NO_MOC or confidence < MOC_REVIEW:
                action = "none"
            else:
                action = "accepted" if confidence >= MOC_AUTO else "review"
            event_id = await self._classification.record_raw(
                result,
                object_type="moc",
                object_id=note["id"],
                action_taken=action,
                confidence=confidence,
                metadata={"moc": slug, **extra},
            )
            if action == "none":
                continue
            moc_id = await self._db.fetchval("SELECT id FROM mocs WHERE slug = $1", slug)
            if moc_id is None:
                continue
            await self._db.execute(
                """
                INSERT INTO moc_memberships (
                    moc_id, note_id, section, confidence, status, classification_event_id
                ) VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (moc_id, note_id) DO UPDATE
                    SET section = EXCLUDED.section, confidence = EXCLUDED.confidence,
                        status = EXCLUDED.status,
                        classification_event_id = EXCLUDED.classification_event_id,
                        updated_at = now()
                """,
                moc_id,
                note["id"],
                section if section in SECTION_TITLES else "other",
                confidence,
                action,
                event_id,
            )
            if action == "accepted":
                report.memberships_accepted += 1
            else:
                report.memberships_review += 1
                await self._classification.queue_review(
                    object_type="moc_membership",
                    object_id=note["id"],
                    reason=f"moc:{slug}",
                    question=f"Put {note['title']!r} on the map {slug!r}?",
                    suggested={"moc": slug, "section": section},
                    confidence=confidence,
                    classification_event_id=event_id,
                )
        return report

    # ---------------------------------------------------------- render

    async def render(self, *, everything: bool = False) -> WikiReport:
        """Queue vault writes for notes and maps whose graph changed since
        they were last rendered. The content itself is produced when the
        worker leases the job (worker_api._render_job)."""
        report = WikiReport()
        queue = self._services.sync_queue
        notes = await self._db.fetch(
            """
            SELECT n.id, n.vault_path, n.vault_synced_sha256
            FROM notes n
            WHERE n.vault_path IS NOT NULL
              AND ($1 OR EXISTS (
                    SELECT 1 FROM note_edges e
                    WHERE e.source_note_id = n.id
                      AND e.updated_at > coalesce(n.wiki_rendered_at, '-infinity'))
                  OR EXISTS (
                    SELECT 1 FROM moc_memberships mm
                    WHERE mm.note_id = n.id
                      AND mm.updated_at > coalesce(n.wiki_rendered_at, '-infinity')))
            """,
            everything,
        )
        for note in notes:
            await queue.enqueue(
                job_type="write_note",
                object_type="note",
                object_id=note["id"],
                vault_path=note["vault_path"],
                payload={"note_id": str(note["id"]), "reason": "wiki"},
                expected_sha256=note["vault_synced_sha256"],
            )
            await self._db.execute(
                "UPDATE notes SET wiki_rendered_at = now() WHERE id = $1", note["id"]
            )
            report.notes_queued += 1

        mocs = await self._db.fetch(
            """
            SELECT m.id, m.title, m.vault_path, m.rendered_sha256
            FROM mocs m
            WHERE EXISTS (SELECT 1 FROM moc_memberships mm
                          WHERE mm.moc_id = m.id AND mm.status = 'accepted')
              AND ($1 OR m.rendered_at IS NULL
                   OR m.updated_at > m.rendered_at
                   OR EXISTS (SELECT 1 FROM moc_memberships mm
                              WHERE mm.moc_id = m.id AND mm.updated_at > m.rendered_at))
            """,
            everything,
        )
        for moc in mocs:
            vault_path = moc["vault_path"] or self._services.vault_paths.moc(moc["title"])
            await queue.enqueue(
                job_type="write_moc",
                object_type="moc",
                object_id=moc["id"],
                vault_path=vault_path,
                payload={"moc_id": str(moc["id"])},
                expected_sha256=moc["rendered_sha256"],
            )
            await self._db.execute(
                "UPDATE mocs SET vault_path = $2, rendered_at = now() WHERE id = $1",
                moc["id"],
                vault_path,
            )
            report.mocs_queued += 1
        return report

    # ---------------------------------------------------------- doctor

    async def doctor(self, *, sample: int = 10) -> dict[str, Any]:
        """Health of the graph: what is disconnected, pending or empty."""
        counts = await self._db.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM notes WHERE vault_path IS NOT NULL) AS notes,
              (SELECT count(*) FROM note_edges WHERE status = 'accepted') AS links,
              (SELECT count(*) FROM note_edges WHERE status = 'review') AS links_in_review,
              (SELECT count(*) FROM mocs) AS mocs,
              (SELECT count(*) FROM mocs m WHERE NOT EXISTS (
                  SELECT 1 FROM moc_memberships mm
                  WHERE mm.moc_id = m.id AND mm.status = 'accepted')) AS empty_mocs,
              (SELECT count(*) FROM moc_memberships WHERE status = 'review')
                  AS memberships_in_review,
              (SELECT count(*) FROM notes n WHERE n.vault_path IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM moc_memberships mm
                  WHERE mm.note_id = n.id AND mm.status = 'accepted')) AS notes_without_map
            """
        )
        orphans = await self._db.fetch(
            """
            SELECT n.title FROM notes n
            WHERE n.vault_path IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM note_edges e WHERE e.status = 'accepted'
                              AND n.id IN (e.source_note_id, e.target_note_id))
              AND NOT EXISTS (SELECT 1 FROM moc_memberships mm
                              WHERE mm.note_id = n.id AND mm.status = 'accepted')
            ORDER BY n.updated_at DESC
            """
        )
        return {
            **dict(counts or {}),
            "orphans": len(orphans),
            "orphan_sample": [row["title"] for row in orphans[:sample]],
        }


# --------------------------------------------------------------------------
# Render context, used by worker_api when a job is leased
# --------------------------------------------------------------------------


async def note_graph(
    services: Services, note_id: UUID
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    related = await services.db.fetch(
        """
        SELECT t.vault_path, t.title, e.relation_type
        FROM note_edges e JOIN notes t ON t.id = e.target_note_id
        WHERE e.source_note_id = $1 AND e.status = 'accepted' AND t.vault_path IS NOT NULL
        ORDER BY lower(t.title)
        """,
        note_id,
    )
    maps = await services.db.fetch(
        """
        SELECT m.vault_path, m.title
        FROM moc_memberships mm JOIN mocs m ON m.id = mm.moc_id
        WHERE mm.note_id = $1 AND mm.status = 'accepted' AND m.vault_path IS NOT NULL
        ORDER BY lower(m.title)
        """,
        note_id,
    )
    return (
        [(stem(r["vault_path"]), r["title"], r["relation_type"]) for r in related],
        [(stem(m["vault_path"]), m["title"]) for m in maps],
    )


async def moc_render_input(services: Services, moc_id: UUID) -> dict[str, Any] | None:
    moc = await services.db.fetchrow("SELECT * FROM mocs WHERE id = $1", moc_id)
    if moc is None:
        return None
    members = await services.db.fetch(
        """
        SELECT mm.section, n.vault_path, n.title
        FROM moc_memberships mm JOIN notes n ON n.id = mm.note_id
        WHERE mm.moc_id = $1 AND mm.status = 'accepted' AND n.vault_path IS NOT NULL
        """,
        moc_id,
    )
    sections: dict[str, list[tuple[str, str]]] = {}
    for member in members:
        sections.setdefault(member["section"], []).append(
            (stem(member["vault_path"]), member["title"])
        )
    children = await services.db.fetch(
        "SELECT vault_path, title FROM mocs WHERE parent_moc_id = $1 AND vault_path IS NOT NULL",
        moc_id,
    )
    parent = await services.db.fetchrow(
        "SELECT vault_path, title FROM mocs WHERE id = $1 AND vault_path IS NOT NULL",
        moc["parent_moc_id"],
    )
    return {
        "moc": dict(moc),
        "sections": sections,
        "children": [(stem(c["vault_path"]), c["title"]) for c in children],
        "parent": (stem(parent["vault_path"]), parent["title"]) if parent else None,
    }
