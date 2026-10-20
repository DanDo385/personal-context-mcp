---
name: vault
description: Queue a note, existing list or session summary for Obsidian when the user says "send this to my vault", "put this in Obsidian", or asks for a vault copy.
---

Use `note_write`, which saves canonical database state and queues the local
worker. Choose one `home`: `projects/{existing-project-slug}`, `lesson/{category}`,
`setup/{tool}`, or `other`. Pass covered concept names in `concepts`; these
become `concept/{name}` tags. Use project_search to resolve project homes.

For a note or session summary, supply a stable title and Markdown content.
Reusing that title updates the same note. Do not claim to capture a transcript
you cannot see. For a list, first use `list_search` and `list_get`, then call
`note_write(title=..., list_ref=...)` with empty content. The server renders the
canonical list with item IDs, so ticking a box completes that same item in the
database. Never copy list items into a prose note. Use list_* to change items.

Read `vault_status`: `queued` means waiting for the worker; `written` means it
acknowledged this version. Say "queued" or "already written" plainly. If disabled,
sensitive, failed, busy or conflicted, say it has not been confirmed written
and use sync_status to investigate. Do not silently retry under a new title.
An existing file cannot be relocated by making a second vault copy.
