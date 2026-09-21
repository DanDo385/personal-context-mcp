---
name: daily-review
description: >
  Run a periodic review of the user's shared context - open tasks, unresolved
  questions, ambiguous routing awaiting a decision, and the health of the
  vault sync. Use when the user asks for a review, a catch-up, or what needs
  attention.
---

# Daily review

## Trigger

- "what should I be working on"
- "catch me up"
- "anything need my attention"
- a scheduled or explicitly requested review

## Required tools

`context_recent`, `project_list`, `list_recent`, `list_get`, `review_list`,
`review_approve`, `review_reclassify`, `review_dismiss`, `sync_status`,
`session_recent`

## Procedure

1. **Recent activity.** `context_recent` for the last several sessions across
   every provider, so the picture is not one app's view.

2. **Active projects.** `project_list(status="active")` - each row carries its
   open task count and live decision count.

3. **Open work.** `list_get` on the task lists that matter, or `list_recent`
   for what changed lately.

4. **Unresolved questions.** `session_recent` returns each summary's
   `open_questions`. These are the things most likely to be quietly dropped.

5. **Clear the review queue.** `review_list` returns items whose routing the
   classifier declined to guess at - an ambiguous project, an uncertain vault
   destination, a sensitive artifact. Least confident first. For each:
   - `review_approve` if the suggestion was right
   - `review_reclassify` with the correct project or destination if not
   - `review_dismiss` if no action is needed

   Reclassifications are recorded against the original classification event,
   which is how the classifier's accuracy is measured over time. It is worth
   doing properly.

6. **Check the sync.** `sync_status`. If `last_worker_activity` is stale, the
   local worker is not running and nothing has reached the vault - say that
   plainly rather than assuming the files are there.

## Safeguards

- Do not approve review items in bulk without reading them. The queue exists
  precisely because these were the uncertain ones.
- Do not mark tasks complete on the user's behalf.
- Report the sync state honestly; "queued" and "written" are different things.

## Outputs

A short structured brief:

- what changed since last time
- active projects and their open counts
- unresolved questions
- items awaiting a routing decision
- whether the vault is actually in sync

Lead with what needs a decision, not with a wall of recent activity.

## Failure behaviour

If `review_list` or `sync_status` errors, report the rest and name what you
could not check. An incomplete review that says which part is missing is more
useful than one that quietly omits it.
