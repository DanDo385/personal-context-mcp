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
ChatGPT should know". Paste the block from `instructions/shared.md`.

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Personalization → Memory**: turn off *Reference saved memories* and *Reference chat history*. Custom instructions stay on.
Personal Context then becomes this app's only long-term memory, shared with the other three.

## 4. Skills / plugin packaging

If your account has account-level Skills, upload the directories under
`skills/`: at minimum `recall-context`, `checkpoint-session`, `manage-lists`,
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
server is at fault: `/healthz` and `personal-context-mcp check` will tell you
whether it is.

**Attachment limitation.** As with every provider, an uploaded file that is
not delivered to tools as bytes is recorded with
`artifact_register_reference` and status `pending_import`.
