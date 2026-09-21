---
name: checkpoint-session
description: >
  Persist durable state from a conversation into shared cross-provider memory.
  Use when something has been settled that should outlive this chat: a
  decision, a confirmed fact, a task, a list change, a project update, or an
  unresolved question.
---

# Checkpoint session

## Trigger

Checkpoint when the conversation has produced something that a future session
would need to be told again:

- a decision was made
- a fact about the user, their work or their preferences was confirmed
- a concrete task was created
- a list changed
- a project's status, scope or goals changed
- a question was raised and left open
- substantive research was done that is worth keeping

Checkpoint **at the point the thing is settled**, not at the end of the
conversation - a chat that ends abruptly should not lose what it established.

## Do not checkpoint

- small talk, jokes, pleasantries
- a lookup that is fully answered in the moment
- your own restatement of something already recorded
- anything you already checkpointed in this conversation
- a draft or an option the user did not accept

Noise is expensive here: it buries the things that matter.

## Required tools

`session_checkpoint` (primary), `list_search` and `project_search` (to find
real names before referring to them)

## Procedure

1. **Look up real names first.** If you are going to touch a list or a
   project, call `list_search` / `project_search` so you use the name that
   exists rather than creating a near-duplicate.

2. **Call `session_checkpoint` ONCE** with everything:

   - `provider` - which app you are
   - `provider_conversation_id` - this conversation's id if you can see it;
     supplying it makes repeat checkpoints update one record
   - `summary` - a few sentences written for a reader who cannot see this
     conversation
   - `decisions` - one sentence each, with rationale if it was discussed
   - `facts` - self-contained, with **accurate** `provenance`
   - `tasks` - concrete unfinished actions
   - `list_updates` - additions and completions on existing lists
   - `project_updates` - status or goal changes
   - `open_questions` - what is still unresolved
   - `artifacts` - ids of artifacts already captured this session

3. **Read the result.** It reports exactly what was created, updated and
   skipped, plus how the content was classified. If `notes` is non-empty, it is
   telling you something you should relay - a list it had to create, an item it
   could not match, an artifact it could not attach.

## Safeguards

- `provenance` must be honest: `user_statement` only for things the user
  actually said. Marking your own inference as the user's statement corrupts
  the record permanently.
- Do not split one checkpoint into several calls. It is one transaction.
- Do not set `sensitivity` to `normal` for medical, financial, legal or
  credential-adjacent content; use `private` or `highly_sensitive`, which
  suppresses external classification and vault writes.

## Failure behaviour

If the call fails, tell the user the checkpoint did not save and what was in
it, so they can decide whether to retry. Do not claim it saved. If the result
says the classifier was degraded, relay that nothing was promoted to their
vault but the data itself was stored.

## Example

> After settling an architecture question:

```
session_checkpoint(
  provider="claude",
  provider_conversation_id="conv_01H...",
  title="Shared AI memory architecture",
  summary="Settled the ownership model: Postgres owns structured state, object
           storage owns file bytes, Obsidian is the curated human layer.",
  decisions=[{
    "statement": "Supabase is canonical structured state and Obsidian is curated knowledge.",
    "rationale": "Postgres gives integrity and query; the vault gives a human interface.",
    "project": "personal-ai"
  }],
  facts=[{
    "content": "Artifact bytes live in Supabase Storage, never in Postgres bytea.",
    "subject": "personal-ai-architecture",
    "provenance": "user_statement"
  }],
  tasks=[{"text": "Test the Gemini connector on iPhone", "list": "personal-ai-tasks"}],
  open_questions=["Does Grok support static OAuth client ids yet?"]
)
```
