# ChatGPT setup

## 1. Enable developer mode

**Settings → Security and login → Developer mode**, switch it on. ChatGPT labels it an elevated-risk
setting because it allows connectors OpenAI hasn't reviewed; this one is your own server.

(Older ChatGPT versions had it under **Apps & Connectors → Advanced settings**. The location moves
from time to time; if neither matches, search Settings for "Developer mode".)

## 2. Add the server as a plugin

**Settings → Plugins → +** (the plus button), then supply:

- **Name:** Personal Context
- **Description:** My shared memory, projects, lists and artifacts
- **Connection:** the MCP server URL, `https://<your-host>/mcp` (the `/mcp` path is required)
- **Authentication:** OAuth, if asked

Create it, sign in (Approve, then Google), and check the list of discovered tools.

To use it, start a new chat and add **Personal Context** from the tools menu (**+** under the message
box). ChatGPT only uses a custom plugin in chats where it's switched on.

ChatGPT connects over public HTTPS only. There is no stdio option, and a
server on your laptop will not work without OpenAI's own tunnel, which is why
the server is put online (docs/guides/05-put-it-online.md) rather than run locally.

## 3. Custom instructions

**Settings → Personalization → Custom instructions**, into "Anything else
ChatGPT should know". Paste this text:

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

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Personalization → Memory**: turn off *Reference saved memories* and *Reference chat history*. Custom instructions stay on.
Personal Context then becomes this app's only long-term memory, shared with the other three.

## 4. Skills / plugin packaging

If your account has account-level Skills, upload the directories under
`skills/`: at minimum `recall-context`, `checkpoint-session`, `manage-lists`,
`capture-artifacts`, `project-artifacts` and `research-to-obsidian`.
To check: go to Settings. If you see a "Skills" section, upload the folders
listed above. If you don't see it, skip this section — your custom instruction
covers the same ground.

## 5. Surfaces and limitations

| | |
| --- | --- |
| Transport | standard web (works automatically) |
| Auth | OAuth (DCR supported) |
| Tools | all 48 |
| Write approval | ChatGPT confirms write actions |
| Mobile (iOS) | connectors configured on web are available in the app |

**Known rough edges.** Occasionally a connector disappears from the list.
If your tools stop working: remove and re-add the connector (30 seconds).
Your server is almost certainly fine — check `/healthz` to confirm.

**Attachment limitation.** As with every provider, an uploaded file that is
not delivered to tools as bytes is recorded with
`artifact_register_reference` and status `pending_import`.
