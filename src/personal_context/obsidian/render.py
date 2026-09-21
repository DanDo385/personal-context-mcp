"""Markdown rendering for the vault.

Lists carry their item UUID in an HTML comment so a checkbox ticked by hand
in Obsidian can be matched back to the canonical row. Identity is never
recovered from the item text.
"""

from __future__ import annotations

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
            "tags": ["personal-context/list"],
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


def render_note(note: dict[str, Any]) -> str:
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
            "tags": ["personal-context/note"],
        }
    )
    parts = [front, "", f"# {note['title']}", "", note["content"].strip(), ""]
    citations = note.get("citations") or []
    if citations:
        parts += ["", "## Sources", ""]
        for citation in citations:
            if isinstance(citation, dict):
                title = citation.get("title") or citation.get("url") or "source"
                url = citation.get("url")
                parts.append(f"- [{title}]({url})" if url else f"- {title}")
            else:
                parts.append(f"- {citation}")
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
            "tags": ["personal-context/artifact"],
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
            "tags": ["personal-context/project"],
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
