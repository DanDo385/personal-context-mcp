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
Paste the block from `instructions/shared.md`.

Claude-specific addition:

```
When you use Personal Context, say briefly what you retrieved or saved, so I
can tell memory from inference.
```

## 3. Skills

Claude supports Agent Skills. Install the workflow skills from `skills/`:

- Claude.ai → **Settings → Capabilities → Skills → Upload skill**, once per
  directory in `skills/`, or
- for Claude Code in this repository, they are already on disk under `skills/`

Keep these separate from TypeSafe's official SDK skill, which is a development
aid for this repository, not a runtime workflow:

```bash
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
```

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth 2.1 + PKCE, Dynamic Client Registration |
| Tools | all 45 |
| Prompts | supported, surfaced as slash-style commands |
| Resources | supported |
| Write approval | Claude asks before the first call to each write tool |
| Mobile (iOS) | connectors configured on web are available in the app; file bytes from the iOS share sheet are **not** passed to tools |

**Attachment limitation.** Files you attach in the Claude apps are visible to
the model but are not delivered to MCP tools as bytes. Claude will call
`artifact_register_reference` for them, which records that they existed with
status `pending_import`. Import your Claude export to fill in the bytes.
