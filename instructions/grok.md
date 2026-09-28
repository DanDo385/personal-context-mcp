# Grok setup

## 1. Connect the MCP server

At `grok.com/connectors`: **New Connector → Custom**, then paste
`https://<your-host>/mcp`.

Grok opens a browser window for OAuth. Sign in with the Google account on your
allowlist and approve the consent screen. Grok then discovers the tools the
server exposes.

**Grok requires Dynamic Client Registration.** Its connector form does not
accept a static OAuth client id, so a server that only supports
pre-registered clients cannot be added at all. This is the binding constraint
across all four providers and the reason this deployment fronts Google OAuth
with a DCR-compliant proxy rather than expecting each provider to be
registered by hand.

Connectors and the custom MCP option are on Grok's paid tiers.

## 2. Persistent instructions

Grok's custom-instruction field (**Settings → Customize**). Paste the block
from `instructions/shared.md`.

## 3. Skills

Where Grok supports skill-like packaging, the directories under `skills/` are
portable Markdown and can be used directly. Where it does not, the MCP tool
descriptions and the `personal-context-mcp://guide/<name>` resources carry the
same guidance.

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Customize** or **Data controls**: turn off *Memory* / *Personalize with memories*.
Personal Context then becomes this app's only long-term memory, shared with the other three.

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth — **DCR required**, static client ids not supported |
| Tools | all 48 |
| Tier | paid tiers only |

**Three separate products.** xAI has more than one surface that connects to
MCP servers — the Grok web app's Connectors, and the xAI API's remote MCP
tools. Their setup does not carry over; adding the connector in the web app
does not make it available to API calls, and vice versa. This document covers
the web app, which is what shares context with the other three assistants.

**Attachment limitation.** Grok's export does not reliably include generated
images. Artifacts that matter should be captured during the conversation;
anything only named is recorded as `pending_import`.
