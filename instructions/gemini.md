# Gemini setup

> **On Google AI Plus or the free tier?** Custom apps aren't available on those plans. See [guide 11](../docs/guides/11-gemini-without-custom-apps.md) for a read-only workaround.

## 1. Connect the MCP server

On a computer, at `gemini.google.com`:

**Settings & help → Connected Apps → Custom apps for Spark → Add a custom app**, then enter
`https://<your-host>/mcp` and click **Next**.

Custom apps are part of **Gemini Spark**, so you need:

- a **Google AI Pro or Ultra** plan. **Google AI Plus and the free tier don't include it**: the
  *Custom apps for Spark* section simply isn't shown in Connected Apps.
- a **personal** Google Account (work and school accounts can't add custom apps)
- **Keep Activity** switched on; custom apps are unavailable with it off
- US-based, 18+, English

Setup happens on desktop only. Once connected, the app is available in both
Gemini web and the Gemini mobile app.

If the connection dialog asks for credentials under **Advanced features →
Show more**, that is the fallback path for servers without Dynamic Client
Registration. This server supports DCR, so you should not need it. If the dialog does show **Advanced features → Show more** with fields for a client ID and secret, check that your server address starts with `https://` (not `http://`) and has no trailing slash. Remove the connector and add it again.

## 2. Instructions

**Settings & help → Saved info** (Gemini's persistent instruction field).
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

## 3. No Skills, and that is fine

Consumer Gemini has no Agent Skills mechanism. This deployment does not
require a Gem, because the point is for *ordinary* Gemini chats to share the
same context.

Gemini doesn't need extra files installed. The instruction you just pasted
tells it when to use the tools, and the tools themselves explain what they do.

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings & help → **Personal context** (or **Past chats**): turn off *Your past chats with Gemini*. **Keep Gemini Apps Activity / Keep Activity on**: custom apps are unavailable without it.
Personal Context then becomes this app's only long-term memory, shared with the other three.

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth with DCR; manual credentials available as a fallback |
| Tools | all 48 |
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
`artifact_save_bundle` / `artifact_save_text`; there is no later import path
for them. This is the one provider where capturing at the time genuinely
matters.
