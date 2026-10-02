"""Markdown rendering for the vault.

Lists carry their item UUID in an HTML comment so a checkbox ticked by hand
in Obsidian can be matched back to the canonical row. Identity is never
recovered from the item text.
"""

from __future__ import annotations

import json
import re
from typing import Any

PC_ID_PREFIX = "pc:id="


def item_marker(item_id: Any) -> str:
    return f"<!-- {PC_ID_PREFIX}{item_id} -->"


def _frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {v}" for v in value)
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            text = str(value).replace("\n", " ")
            lines.append(f'{key}: "{text}"' if ":" in text or "#" in text else f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines)


def render_list(list_row: dict[str, Any], items: list[dict[str, Any]]) -> str:
    front = _frontmatter(
        {
            "pc_type": "list",
            "pc_id": list_row["id"],
            "title": list_row["name"],
            "list_type": list_row.get("list_type"),
            "project": list_row.get("project_slug"),
            "updated": list_row.get("updated_at"),
            "tags": ["personal-context-mcp/list", "type/list"],
        }
    )
    body = [front, "", f"# {list_row['name']}", ""]
    if list_row.get("description"):
        body += [list_row["description"], ""]

    open_items = [i for i in items if i["status"] == "open"]
    done_items = [i for i in items if i["status"] == "done"]
    blocked = [i for i in items if i["status"] == "blocked"]

    def render_group(title: str, group: list[dict[str, Any]], checked: bool) -> list[str]:
        if not group:
            return []
        lines = [f"## {title}", ""]
        for item in group:
            box = "x" if checked else " "
            suffix = f" (due {item['due_date']})" if item.get("due_date") else ""
            lines.append(f"- [{box}] {item['text']}{suffix} {item_marker(item['id'])}")
            if item.get("notes"):
                lines.append(f"      {item['notes']}")
        lines.append("")
        return lines

    body += render_group("Open", open_items, checked=False)
    body += render_group("Blocked", blocked, checked=False)
    body += render_group("Done", done_items, checked=True)
    body += [
        "",
        "<!-- Managed by Personal Context. Tick a box here and the change flows",
        "     back to the canonical list; do not edit the pc:id comments. -->",
        "",
    ]
    return "\n".join(body)


#: Everything between these markers is regenerated on every write and is
#: stripped from a human edit before it is stored, so links and sources never
#: leak into the canonical body.
GENERATED_START = (
    "<!-- pc:generated - maintained by personal-context-mcp; edits here are replaced -->"
)
GENERATED_END = "<!-- /pc:generated -->"

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n?", re.S)
_GENERATED = re.compile(
    re.escape(GENERATED_START.split(" - ")[0]) + r".*?" + re.escape(GENERATED_END), re.S
)


def wikilink(target_stem: str, title: str) -> str:
    """Vault files are named by slug, so the link names the file and shows
    the title."""
    clean = title.replace("]", "").replace("|", "-").strip()
    return f"[[{target_stem}|{clean}]]" if clean and clean != target_stem else f"[[{target_stem}]]"


def render_note(
    note: dict[str, Any],
    *,
    related: list[tuple[str, str, str]] | None = None,
    mocs: list[tuple[str, str]] | None = None,
) -> str:
    """``related`` is (file stem, title, relation); ``mocs`` is (stem, title)."""
    meta = note.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    tags = ["personal-context-mcp/note", *[t for t in meta.get("tags") or [] if isinstance(t, str)]]
    if note.get("source_provider"):
        tags.append(f"from/{note['source_provider']}")
    type_tag = {"project": "type/project", "session_index": "type/session-index"}.get(
        note.get("note_class") or ""
    )
    if type_tag and type_tag not in tags:
        tags.append(type_tag)
    front = _frontmatter(
        {
            "pc_type": "note",
            "pc_id": note["id"],
            "title": note["title"],
            "note_class": note.get("note_class"),
            "project": note.get("project_slug"),
            "source_provider": note.get("source_provider"),
            "source_conversation": note.get("source_conversation_id"),
            "provenance": note.get("provenance"),
            "version": note.get("version"),
            "updated": note.get("updated_at"),
            "source_date": meta.get("source_date"),
            "tags": tags,
        }
    )
    parts = [front, "", f"# {note['title']}", "", note["content"].strip(), ""]

    generated: list[str] = []
    if mocs:
        generated += ["## Maps", ""]
        generated += [f"- {wikilink(stem, title)}" for stem, title in mocs]
        generated.append("")
    if related:
        generated += ["## Related", ""]
        generated += [
            f"- {wikilink(stem, title)} · {relation.replace('_', ' ')}"
            for stem, title, relation in related
        ]
        generated.append("")
    citations = note.get("citations") or []
    if citations:
        generated += ["## Sources", ""]
        for citation in citations:
            if isinstance(citation, dict):
                title = citation.get("title") or citation.get("url") or "source"
                url = citation.get("url")
                generated.append(f"- [{title}]({url})" if url else f"- {title}")
            else:
                generated.append(f"- {citation}")
        generated.append("")
    if note.get("source_provider") and note.get("source_conversation_id"):
        when = f" on {meta['source_date']}" if meta.get("source_date") else ""
        generated += [f"*From a {note['source_provider'].title()} conversation{when}.*", ""]
    if generated:
        parts += ["", GENERATED_START, "", *generated, GENERATED_END, ""]
    return "\n".join(parts)


def extract_note_body(text: str, title: str | None = None) -> str:
    """Recover the human-owned body from a rendered note file.

    Strips the frontmatter, the ``# Title`` heading the renderer adds, and the
    generated block. Files rendered before the generated block existed end
    with a bare ``## Sources`` section, which is stripped too.
    """
    body = _FRONTMATTER.sub("", text.replace("\r\n", "\n"), count=1)
    body = _GENERATED.sub("", body)
    lines = body.strip("\n").split("\n")
    if lines and lines[0].startswith("# ") and (title is None or lines[0][2:].strip() == title):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    legacy = body.rfind("\n## Sources\n")
    if legacy != -1 and "\n## " not in body[legacy + 1 :].split("\n", 1)[-1]:
        body = body[:legacy].rstrip()
    return body


def render_moc(
    moc: dict[str, Any],
    sections: dict[str, list[tuple[str, str]]],
    *,
    section_titles: dict[str, str],
    children: list[tuple[str, str]] | None = None,
    parent: tuple[str, str] | None = None,
    lists: list[tuple[str, str]] | None = None,
) -> str:
    """A Map of Content: sections in the order of ``section_titles``, entries
    sorted by title. Entirely generated - a MOC has no human-owned body."""
    front = _frontmatter(
        {
            "pc_type": "moc",
            "pc_id": moc["id"],
            "title": moc["title"],
            "updated": moc.get("updated_at"),
            "tags": ["personal-context-mcp/moc", "type/moc"],
        }
    )
    parts = [front, "", f"# {moc['title']}", ""]
    if moc.get("description"):
        parts += [str(moc["description"]).strip(), ""]
    if parent:
        parts += [f"Part of {wikilink(*parent)}.", ""]
    for key, heading in section_titles.items():
        entries = sections.get(key) or []
        if not entries:
            continue
        parts += [f"## {heading}", ""]
        parts += [
            f"- {wikilink(stem, title)}"
            for stem, title in sorted(entries, key=lambda e: e[1].lower())
        ]
        parts.append("")
    if lists:
        parts += ["## Lists", ""]
        parts += [
            f"- {wikilink(stem, title)}"
            for stem, title in sorted(lists, key=lambda entry: entry[1].lower())
        ]
        parts.append("")
    if children:
        parts += ["## Sub-maps", ""]
        parts += [
            f"- {wikilink(stem, title)}"
            for stem, title in sorted(children, key=lambda e: e[1].lower())
        ]
        parts.append("")
    return "\n".join(parts)


def render_artifact_note(
    artifact: dict[str, Any], files: list[dict[str, Any]], *, download_hint: str
) -> str:
    front = _frontmatter(
        {
            "pc_type": "artifact",
            "pc_id": artifact["id"],
            "title": artifact["title"],
            "artifact_type": artifact.get("artifact_type"),
            "project": artifact.get("project_slug"),
            "source_provider": artifact.get("source_provider"),
            "source_conversation": artifact.get("source_conversation_id"),
            "storage_status": artifact.get("storage_status"),
            "version": artifact.get("version"),
            "created": artifact.get("created_at"),
            "tags": ["personal-context-mcp/artifact"],
        }
    )
    lines = [front, "", f"# {artifact['title']}", ""]
    if artifact.get("description"):
        lines += [artifact["description"], ""]

    status = artifact.get("storage_status")
    if status != "captured":
        lines += [
            f"> **Bytes not held.** This artifact is recorded with status `{status}`: "
            "its metadata is known but the file contents were never captured. "
            "Import the provider export to fill it in.",
            "",
        ]

    lines += ["## Files", "", "| Path | Type | Size | SHA-256 |", "| --- | --- | ---: | --- |"]
    for f in files:
        size_kb = f"{f['byte_size'] / 1024:.1f} KB" if f["byte_size"] else "-"
        lines.append(
            f"| `{f['relative_path']}` | {f['mime_type']} | {size_kb} | `{f['sha256'][:12]}…` |"
        )
    lines += ["", "## Retrieve", "", download_hint, ""]
    return "\n".join(lines)


def render_project(project: dict[str, Any], snapshot: dict[str, Any]) -> str:
    front = _frontmatter(
        {
            "pc_type": "project",
            "pc_id": project["id"],
            "title": project["name"],
            "status": project.get("status"),
            "updated": project.get("updated_at"),
            "tags": ["personal-context-mcp/project"],
        }
    )
    lines = [front, "", f"# {project['name']}", ""]
    if project.get("description"):
        lines += [project["description"], ""]
    if project.get("goals"):
        lines += ["## Goals", "", project["goals"], ""]

    decisions = snapshot.get("decisions") or []
    if decisions:
        lines += ["## Decisions", ""]
        lines += [f"- {d['statement']} _({d['decided_at']:%Y-%m-%d})_" for d in decisions]
        lines.append("")

    tasks = snapshot.get("open_tasks") or []
    if tasks:
        lines += ["## Open tasks", ""]
        lines += [
            f"- [ ] {t['text']}" + (f" (due {t['due_date']})" if t.get("due_date") else "")
            for t in tasks
        ]
        lines.append("")

    events = snapshot.get("recent_events") or []
    if events:
        lines += ["## Recent activity", ""]
        lines += [f"- _{e['created_at']:%Y-%m-%d}_ — {e['summary']}" for e in events]
        lines.append("")
    return "\n".join(lines)
