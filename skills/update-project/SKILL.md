---
name: update-project
description: >
  Keep a project's state current - status, goals, decisions, open tasks and
  activity log. Use when work moves forward, changes direction, stalls, or
  completes.
---

# Update project

## Trigger

- a project's status changed (started, paused, blocked, finished)
- its goals or scope changed
- a milestone was reached
- a decision was made that the project rests on
- the user asks "where am I on X"

## Required tools

`project_search`, `project_get`, `project_create`, `project_update`,
`project_event_add`, `session_checkpoint`

## Procedure

1. **Find it first.** `project_search` before anything else. A second project
   for work that already has one splits its history, its artifacts and its
   tasks across two graph nodes.

2. **Read before writing.** `project_get` returns goals, live decisions, open
   tasks and recent events - everything needed to answer "where am I" without
   guessing.

3. **Update what changed.** `project_update` for status, goals or description.
   Only pass the fields that actually changed; omitted fields are left alone.

4. **Log the event.** `project_event_add` with one line on what happened.
   Identical events are not repeated, so a re-run is harmless. This is what
   makes the project's history readable later.

5. **Prefer a checkpoint when several things changed.** If the conversation
   also produced decisions, facts or tasks, do all of it in one
   `session_checkpoint` with `project_updates` rather than several calls.

## Safeguards

- Do not mark a project `done` unless the user said so. Inferring completion
  from a successful-sounding conversation is a common and annoying error.
- Do not rewrite `goals` wholesale when the user only refined one point -
  read the current value first and extend it.
- Creating a project is cheap but merging two is not. When in doubt, ask.

## Outputs

State what changed and what the project now looks like: "personal-ai is now
`active` with 7 open tasks and 3 live decisions."

## Example

> "The dashboard's done, I'm moving on to the importers."

```
project_update(project="personal-ai-dashboard", status="done")
project_event_add(project="personal-ai-dashboard", event_type="milestone",
                  summary="Dashboard shipped")
project_update(project="personal-ai", goals="Bulk import of provider exports")
```
