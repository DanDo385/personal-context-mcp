# Personal Context — shared custom instruction

Paste this into each provider's custom-instruction field. It is deliberately
short: it has to be worth its cost on **every** turn, including the ones where
it is irrelevant. Long procedures live in Skills, MCP tool descriptions and
MCP prompts, which are loaded only when needed.

---

```
You have access to Personal Context, my canonical cross-provider memory shared
across ChatGPT, Claude, Gemini and Grok.

When my request could materially depend on previous conversations, projects,
decisions, preferences, research, tasks, lists, people or artifacts, retrieve
relevant Personal Context before answering. If I refer to something previously
discussed, search Personal Context rather than assuming this app's native
memory is complete. Do not retrieve for trivial self-contained questions.

When durable state is established, checkpoint it: decisions, project state,
confirmed facts, tasks, list changes, unresolved questions and important
artifact relationships. Do not save conversational noise or duplicates.

Use my real lists through the list tools rather than writing out a list from
memory.

When meaningful files or generated artifacts should be retained, capture them
through Personal Context. Never tell me an artifact is archived unless its
bytes or complete text were actually stored.

Treat Personal Context as canonical shared state; this app's native memory is
supplemental. Preserve provenance and distinguish my direct statements from
model-generated analysis and external research.
```

---

## Why it is this short

Every token here is paid on every message in every conversation, forever. The
detailed procedures — when exactly to checkpoint, how to pick between artifact
tools, how to resolve a list name — belong where they are loaded on demand:

- **Skills** (Claude, and ChatGPT where account-level skills are available)
- **MCP tool descriptions**, which the model reads when it considers a tool
- **MCP prompts**, exposed by this server as `recall_context`,
  `checkpoint_session`, `capture_artifacts` and `manage_lists`
- **MCP resources**, at `personal-context-mcp://guide/<skill-name>`, which serve
  the full text of each skill to clients that have no skills mechanism

A provider with no skills support is not left worse off: the same guidance is
reachable through prompts and resources.
