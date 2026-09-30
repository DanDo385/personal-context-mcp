# Grok setup

## 1. Connect the MCP server

Open **https://grok.com/connectors** (the Grok chat site, not your xAI account page at
accounts.x.ai, which only lists sign-ins and apps authorized to your account). Click
**New Connector → Custom**, then paste `https://<your-host>/mcp`.

Grok opens a browser window for OAuth. Sign in with the Google account on your
allowlist and approve the consent screen. Grok then discovers the tools the
server exposes.

**Grok handles the sign-in automatically through your server.** You don't need to enter any client ID or secret — just paste the URL and sign in with Google.

Connectors and the custom MCP option are on Grok's paid tiers.

## 2. Persistent instructions

Grok's custom-instruction field (**Settings → Customize**). Paste this text:

```
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

## 3. Skills

Grok doesn't support skills files. The instruction you pasted in step 2 is enough — it tells Grok when to use its new tools.

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Customize** or **Data controls**: turn off *Memory* / *Personalize with memories*.
Personal Context then becomes this app's only long-term memory, shared with the other three.

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth (your server handles sign-in automatically) |
| Tools | all 48 |
| Tier | paid tiers only |

**Three separate products.** You only need the Grok web app connector (grok.com). The API and developer tools are separate and not covered here. xAI has more than one surface that connects to MCP servers: the Grok web app's Connectors, and the xAI API's remote MCP
tools. Their setup does not carry over; adding the connector in the web app
does not make it available to API calls, and vice versa. This document covers
the web app, which is what shares context with the other three assistants.

**Attachment limitation.** Grok's export does not reliably include generated
images. Artifacts that matter should be captured during the conversation;
anything only named is recorded as `pending_import`.
