# Claude setup

## 1. Connect the MCP server

Claude web/desktop → **Settings → Connectors → Add custom connector**.

- **Name:** Personal Context
- **URL:** `https://<your-host>/mcp` (or `/mcp/<server-id>` if you set one)

Claude discovers the OAuth metadata from
`/.well-known/oauth-protected-resource`, registers itself dynamically, and
opens a browser window for you to sign in with the Google account on your
allowlist. Approve the consent screen once.

Claude supports Dynamic Client Registration, so no client id needs to be
configured by hand.

## 2. Custom instructions

Settings → **Profile → What personal preferences should Claude consider**.
Paste this text:

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

> ⚠️ **Also paste this extra line after the shared instruction:**
>
> When you use Personal Context, say briefly what you retrieved or saved, so I can tell memory from inference.

## 3. Skills

Claude supports Agent Skills. Install the workflow skills from `skills/`:

- Claude.ai → **Settings → Capabilities → Skills → Upload skill**, once per
  directory in `skills/`, or
- for Claude Code in this repository, they are already on disk under `skills/`

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Capabilities** (or **Memory**): turn off *Generate memory from chat history* and *Search and reference chats*. Keep the Personal Context connector enabled.
Personal Context then becomes this app's only long-term memory, shared with the other three.

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth 2.1 + PKCE, Dynamic Client Registration |
| Tools | all 48 |
| Prompts | supported, surfaced as slash-style commands |
| Resources | supported |
| Write approval | Claude asks before the first call to each write tool |
| Mobile (iOS) | connectors configured on web are available in the app; file bytes from the iOS share sheet are **not** passed to tools |

**Attachment limitation.** Files you attach in the Claude apps are visible to
the model but are not delivered to MCP tools as bytes. Claude will call
`artifact_register_reference` for them, which records that they existed with
status `pending_import`. Import your Claude export to fill in the bytes.

## For developers only

Skip this unless you're working on the personal-context-mcp code itself.

Keep these separate from TypeSafe's official SDK skill, which is a development
aid for this repository, not a runtime workflow:

```bash
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
```
