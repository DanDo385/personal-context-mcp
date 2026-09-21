# Gemini setup

## 1. Connect the MCP server

On a computer, at `gemini.google.com`:

**Settings & help → Connected Apps → Custom apps → Add a custom app**, then
enter `https://<your-host>/mcp`.

Requirements Google currently imposes:

- a **personal** Google Account — work and school accounts cannot add custom
  apps
- **Keep Activity** switched on; custom apps are unavailable with it off
- US-based, 18+, English

The custom-app control appears in Connected Apps under the Spark heading, but
adding a custom MCP server does **not** require a Spark subscription — it is
available to ordinary personal Gemini accounts, which is why this setup uses
it rather than requiring a Gem.

Setup happens on desktop only. Once connected, the app is available in both
Gemini web and the Gemini mobile app.

If the connection dialog asks for credentials under **Advanced features →
Show more**, that is the fallback path for servers without Dynamic Client
Registration. This server supports DCR, so you should not need it.

## 2. Instructions

**Settings & help → Saved info** (Gemini's persistent instruction field).
Paste the block from `instructions/shared.md`.

## 3. No Skills — and that is fine

Consumer Gemini has no Agent Skills mechanism. This deployment does not
require a Gem, because the point is for *ordinary* Gemini chats to share the
same context. Workflow guidance reaches Gemini three ways instead:

1. the saved instruction above
2. MCP tool descriptions, written to stand on their own
3. MCP prompts (`recall_context`, `checkpoint_session`, `capture_artifacts`,
   `manage_lists`) and the `personal-context://guide/<name>` resources, which
   serve the full text of each skill on demand

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth with DCR; manual credentials available as a fallback |
| Tools | all 45 |
| Prompts / resources | availability varies; tool descriptions carry the load |
| Write approval | **Gemini requires manual confirmation for every write action** |
| Mobile | available after desktop setup |
| Language | English only at present |

**Write confirmation.** Gemini prompts before each write. Expect
`session_checkpoint` and the artifact tools to require a tap. This is a
reasonable default and worth leaving on.

**Attachment limitation.** Google Takeout does not export files generated in
Gemini, and gives no filename to reference them by. Artifacts created in
Gemini therefore need to be captured *during* the conversation through
`artifact_save_bundle` / `artifact_save_text` — there is no later import path
for them. This is the one provider where capturing at the time genuinely
matters.
