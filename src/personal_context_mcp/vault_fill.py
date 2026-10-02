"""Fill the vault from rows already stored.

Session indexes, the Home map, and one area map per project that already has
a note. Exact-title links only. Nothing here calls a model or Jev.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from personal_context_mcp.importers.sensitivity import raise_to
from personal_context_mcp.services import Services
from personal_context_mcp.sessions.index import (
    PROVIDER_TITLES,
    SessionIndexRow,
    index_date,
    index_sensitivity,
    prepare_session_index,
    render_session_index,
)
from personal_context_mcp.util import slugify
from personal_context_mcp.wiki.service import WikiService

_LEVELS = frozenset({"normal", "private", "highly_sensitive"})
_RESERVED_MOC_SLUGS = {slugify(title) for _provider, title in PROVIDER_TITLES} | {"home"}

_SECTIONS = {
    "project": "implementations",
    "research": "research",
    "wiki": "reference",
    "concept": "fundamentals",
    "resource": "reference",
    "session_index": "reference",
}

_INDEX_SQL = """
SELECT c.provider,
       c.provider_conversation_id,
       c.title,
       c.started_at,
       c.sensitivity::text AS conversation_sensitivity,
       s.summary,
       s.sensitivity::text AS summary_sensitivity,
       CASE WHEN EXISTS (
           SELECT 1 FROM messages m WHERE m.conversation_id = c.id
       ) THEN 1 ELSE 0 END AS message_count,
       note.vault_path AS note_vault_path
FROM conversations c
LEFT JOIN session_summaries s ON s.conversation_id = c.id
LEFT JOIN LATERAL (
    SELECT n.vault_path
    FROM notes n
    WHERE n.source_conversation_id = c.id AND n.vault_path IS NOT NULL
    ORDER BY n.updated_at DESC
    LIMIT 1
) note ON true
WHERE c.provider = ANY($1::text[])
  AND c.provider_conversation_id IS NOT NULL
"""


def _level(value: str | None) -> str:
    return value if value in _LEVELS else "normal"


def _section_for(note_class: str) -> str:
    return _SECTIONS.get(note_class, "other")


def _area_title(name: str) -> str:
    title = name.strip() or "Project"
    if slugify(title) in _RESERVED_MOC_SLUGS:
        title = f"{title} project"
    if slugify(title) in _RESERVED_MOC_SLUGS:
        title = f"{title} map"
    return title


def _rows_from(records: list[Any]) -> dict[str, list[SessionIndexRow]]:
    grouped: dict[str, list[SessionIndexRow]] = {
        provider: [] for provider, _title in PROVIDER_TITLES
    }
    for record in records:
        provider = record["provider"]
        if provider not in grouped:
            continue
        sensitivity = raise_to(
            _level(record["summary_sensitivity"]),
            _level(record["conversation_sensitivity"]),
        )
        note_path = record["note_vault_path"]
        grouped[provider].append(
            SessionIndexRow(
                provider=provider,
                provider_conversation_id=record["provider_conversation_id"],
                title=record["title"] or "Untitled",
                occurred_on=index_date(
                    provider, record["provider_conversation_id"], record["started_at"]
                ),
                summary=record["summary"],
                message_count=int(record["message_count"] or 0),
                note_stem=PurePosixPath(note_path).stem if note_path else None,
                sensitivity=sensitivity,
            )
        )
    return grouped


async def _write_index(
    services: Services, provider: str, title: str, rows: list[SessionIndexRow]
) -> dict[str, Any]:
    slug = slugify(title)
    existing = await services.db.fetchrow("SELECT id, note_class FROM notes WHERE slug = $1", slug)
    if existing is not None and existing["note_class"] != "session_index":
        raise RuntimeError(
            f"Refusing to replace an existing {existing['note_class']} note "
            f"with the {title} session index."
        )
    shown, older = prepare_session_index(rows)
    skipped = sum(1 for row in rows if row.message_count <= 0)
    body = render_session_index(provider, rows)
    path = services.vault_paths.session_index(title)
    sensitivity = index_sensitivity(rows)
    note_id, created, version = await services.notes.upsert(
        title=title,
        content=body,
        note_class="session_index",
        vault_path=path,
        sensitivity=sensitivity,
        provenance="system",
        author="system",
    )
    await services.db.execute(
        """
        UPDATE notes
        SET note_class = 'session_index',
            vault_path = $2,
            sensitivity = $3::pc_sensitivity
        WHERE id = $1
        """,
        note_id,
        path,
        sensitivity,
    )
    return {
        "provider": provider,
        "shown": len(shown),
        "older": older,
        "skipped_without_messages": skipped,
        "created": created,
        "version": version,
    }


async def fill_vault(services: Services) -> dict[str, Any]:
    """Queue missing list renders, write indexes and maps, then render the wiki."""
    lists_queued = await services.lists.queue_unrendered()
    providers = [provider for provider, _title in PROVIDER_TITLES]
    records = await services.db.fetch(_INDEX_SQL, providers)
    grouped = _rows_from(records)
    indexes = [
        await _write_index(services, provider, title, grouped[provider])
        for provider, title in PROVIDER_TITLES
    ]

    wiki = WikiService(services)
    home_id = await wiki.add_moc(
        "Home",
        description="Session indexes, lists, and notes already in the vault.",
    )
    home_path = services.vault_paths.moc("Home")
    await services.db.execute("UPDATE mocs SET vault_path = $2 WHERE id = $1", home_id, home_path)

    projects = await services.db.fetch(
        """
        SELECT p.id, p.name, p.description
        FROM projects p
        WHERE EXISTS (
            SELECT 1 FROM notes n
            WHERE n.project_id = p.id
              AND n.note_class IS DISTINCT FROM 'session_index'
              AND n.vault_path IS NOT NULL
        )
        ORDER BY lower(p.name)
        """
    )
    area_ids: dict[Any, Any] = {}
    moc_titles = ["Home"]
    for project in projects:
        title = _area_title(project["name"])
        moc_id = await wiki.add_moc(
            title,
            description=(project["description"] or "").strip() or None,
            parent="Home",
        )
        path = services.vault_paths.moc(title)
        await services.db.execute("UPDATE mocs SET vault_path = $2 WHERE id = $1", moc_id, path)
        area_ids[project["id"]] = moc_id
        moc_titles.append(title)

    notes = await services.db.fetch(
        """
        SELECT id, note_class, project_id
        FROM notes
        WHERE vault_path IS NOT NULL
        """
    )
    memberships = 0
    for note in notes:
        section = _section_for(note["note_class"])
        if await wiki.place_note(moc_id=home_id, note_id=note["id"], section=section):
            memberships += 1
        area_id = area_ids.get(note["project_id"])
        if (
            note["note_class"] != "session_index"
            and area_id is not None
            and await wiki.place_note(moc_id=area_id, note_id=note["id"], section=section)
        ):
            memberships += 1

    links = await wiki.link_exact_titles()
    rendered = await wiki.render()
    return {
        "lists_queued": lists_queued,
        "indexes": indexes,
        "mocs": moc_titles,
        "memberships": memberships,
        "title_links": links.title_links,
        "notes_queued": rendered.notes_queued,
        "mocs_queued": rendered.mocs_queued,
    }
