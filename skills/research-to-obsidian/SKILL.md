---
name: research-to-obsidian
description: >
  Turn substantive research into a durable, human-readable note in the user's
  Obsidian vault. Use when work is worth reading again later, away from the
  conversation that produced it.
---

# Research to Obsidian

## Trigger

- you did substantive research the user will want again
- a topic was explained well enough to be worth keeping as reference
- a decision has enough reasoning behind it to deserve a written record
- the user says "write this up", "save this", "make a note"

Do **not** create a note for something already fully captured as structured
state (a decision, a task, a list item). Those are retrievable already; a note
duplicates them into a second place that can drift.

## Required tools

`note_search`, `note_get`, `note_write`, `project_search`, `sync_status`

## Procedure

1. **Look for an existing note.** `note_search` on the topic. Revisiting a
   subject should extend the existing note, not create "Attention 2".

2. **Write or extend.** `note_write(title=..., content=...)`. Reusing a title
   updates that note and bumps its version. Pass `append=true` to add a new
   section rather than replace the body.

3. **Let the classifier choose the section** by leaving `note_class` unset,
   unless the user was explicit. It routes to project / research / wiki /
   concept / resource / daily / inbox, and falls back to inbox when genuinely
   ambiguous rather than guessing.

4. **Preserve citations.** Pass `citations` as `[{"title": ..., "url": ...}]`.
   Research without sources is much less useful six months later.

5. **Set sensitivity honestly.** Anything medical, financial, legal or
   credential-adjacent should be `private` or `highly_sensitive`, which stops
   it being written to the vault and stops it being sent to the external
   classifier.

6. **Report queued, not written.** The note is stored canonically in the
   database immediately. The vault copy appears only when the user's local
   sync worker next runs. `sync_status` will tell you whether that worker is
   alive if the user asks where their file is.

## Safeguards

- You never choose a filesystem path. You supply a title and a class; the
  server builds and validates the path.
- Never write a note that contradicts an existing one without saying so.
  `note_search` first, and if you find a conflict, surface it.
- Large verbatim transcripts do not belong in notes. Summarise and link the
  conversation.

## Failure behaviour

If `note_write` reports `queued_for_vault: false`, say why - sensitivity, or
the vault being disabled - rather than implying the file exists.

## Example

> After researching MCP OAuth requirements across four providers:

```
note_write(
  title="MCP OAuth support across providers",
  content="## Summary\n\nGrok requires Dynamic Client Registration...\n",
  citations=[
    {"title": "MCP Authorization spec", "url": "https://modelcontextprotocol.io/..."},
    {"title": "Gemini custom apps", "url": "https://support.google.com/gemini/..."}
  ]
)
```
→ "Written as a research note and queued for your vault at
30-Research/mcp-oauth-support-across-providers.md."
