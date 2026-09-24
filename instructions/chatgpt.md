# ChatGPT setup

## 1. Enable developer mode

**Settings → Apps & Connectors → Advanced settings → Developer mode.**

(The location has moved more than once; if it is not under Advanced settings,
look for "Developer mode" elsewhere in Apps & Connectors.)

## 2. Create the connector

**Apps & Connectors → Create**, then supply:

- **Name:** Personal Context
- **Description:** My shared memory, projects, lists and artifacts
- **MCP server URL:** `https://<your-host>/mcp` — the `/mcp` path is required
- **Authentication:** OAuth

ChatGPT connects over public HTTPS only. There is no stdio option, and a
server on your laptop will not work without OpenAI's own tunnel — which is why
the server is put online (docs/guides/05-put-it-online.md) rather than run locally.

## 3. Custom instructions

**Settings → Personalization → Custom instructions**, into "Anything else
ChatGPT should know". Paste the block from `instructions/shared.md`.

## 4. Skills / plugin packaging

If your account has account-level Skills, upload the directories under
`skills/` — at minimum `recall-context`, `checkpoint-session`, `manage-lists`,
`capture-artifacts`, `project-artifacts` and `research-to-obsidian`.

If it does not, nothing is blocked. The same guidance is reachable three other
ways, in descending order of how reliably ChatGPT uses them:

1. the custom instruction above
2. MCP tool descriptions, which are written to be self-sufficient
3. MCP prompts and the `personal-context-mcp://guide/<name>` resources

## 5. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP and SSE |
| Auth | OAuth (DCR supported) |
| Tools | all 48 |
| Write approval | ChatGPT confirms write actions |
| Mobile (iOS) | connectors configured on web are available in the app |

**Known rough edges**, reported by developers through 2026: custom connectors
occasionally disappear from the directory and need re-adding; OAuth sometimes
completes without the connector becoming available in chat until a reload. If
tools stop appearing, disconnect and re-add the connector before assuming the
server is at fault — `/healthz` and `personal-context-mcp check` will tell you
whether it is.

**Attachment limitation.** As with every provider, an uploaded file that is
not delivered to tools as bytes is recorded with
`artifact_register_reference` and status `pending_import`.
