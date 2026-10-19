---
name: recall-context
description: >
  Retrieve the user's shared cross-provider context before answering. Use when
  the request depends on past work - a project, earlier decisions, lists,
  preferences, a continuing task - and always when the user says "we
  discussed", "remember", "continue", "last time", or "what did we decide".
  Never for standalone questions.
---

# Recall context

## Trigger

Retrieve when **any** of these hold:

- the user refers to something previously discussed, explicitly or by implication
- the request continues work on a project
- the answer depends on a preference, a decision, or a person you would only
  know from earlier conversations
- the user asks what is on a list, what is outstanding, or what was decided
- you are about to say "I don't have access to previous conversations" - you do

Do **not** retrieve for a self-contained question ("what's 12% of 340",
"rewrite this paragraph"). Retrieval costs a round trip and dilutes context.

## Required tools

`context_bootstrap`, `context_search`, `context_recent`, `context_for_topic`,
`project_get`, `list_get`, `memory_search`

## Procedure

1. **Start with one bootstrap.** Call `context_bootstrap` with a short `query`
   of key terms you choose, the way you would search your own memory: names,
   projects, topics, ids (`"harbor notes deploy"`, not the user's paragraph).
   Pass `project` when you know it; otherwise the server infers it from the
   query and says which project it picked and why. Never paste the user's
   message: it is reduced to key terms anyway, and the extra words cost tokens.
   The package starts with the project's handoff (the latest session summaries
   with their open questions, open tasks, active decisions), then items that
   match the query, then raw transcript excerpts only if nothing else matched.
   `layers` says which of these ran and why the others did not.

2. **Narrow if needed.** If the package did not answer the question, call
   `context_search` with a sharper query. Add `project=` when you know which
   project. Leave `include_messages=false` unless you need someone's exact
   words - summaries and memories carry the same content at a fraction of the
   size.

3. **Go specific.** For "what's on my X list" call `list_get` directly. For
   "where is project Y" call `project_get`. These are cheaper and exact.

4. **Read the provenance on every item.**
   - `user_statement` - the user said this. Treat it as fact.
   - `assistant_generated` - a model concluded this. Treat it as a hypothesis,
     and say so if you rely on it.
   - `external_source` - it came from research. Cite it as such.
   - `imported_metadata` - derived mechanically from an import, not written by
     anyone. Weakest of the four.

5. **Use it visibly.** Say what you found: "Last month you decided X, in a
   Claude conversation." Silent retrieval leaves the user unable to tell
   whether you actually remembered or guessed.

## Safeguards

- Never present an `assistant_generated` memory as something the user said.
- If two retrieved items conflict, surface the conflict with dates rather than
  silently picking the newer one. The package lists the ones it noticed under
  `conflicts`; a decision marked `superseded` has been replaced by
  `superseded_by`.
- Do not dump the whole package into your reply. Use what is relevant.

## Failure behaviour

If retrieval returns nothing, say so plainly - "I don't have anything recorded
about that" - and answer from the current conversation. Never invent a
remembered detail. If the tool errors, say Personal Context is unreachable and
continue without it; do not retry in a loop.

## Examples

> "What did we decide about the memory architecture?"

`context_bootstrap(query="memory architecture decision")` → returns the
decision with its date, project and originating provider. Answer with the
decision and note which assistant it was made in.

> "Where did we leave off on Harbor Notes?"

`context_bootstrap(project="harbor-notes")` with no query → the handoff alone:
latest sessions, open questions, open tasks, active decisions.

> "Add this to my reading list."

Skip `context_bootstrap`. Go to `list_search("reading")` then `list_add_item`.
