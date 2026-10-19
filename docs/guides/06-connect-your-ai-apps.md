# 6. Connect ChatGPT, Claude, Gemini and Grok

Each app is connected **once, on a computer, in its website**. After that the connection also works in
the app's phone version. Every app follows the same three steps:

1. **Add a connector** with your server's address followed by `/mcp`, for example
   `https://personal-context-mcp.vercel.app/mcp`.
2. **Sign in with Google** in the window that opens, choosing the account whose email you gave the
   setup wizard.
3. **Paste the custom instruction** so the app knows when to use its new memory.

## Check the server first (1 minute)

Before adding it to any app, let your computer connect to the server the same way the apps do:

```bash
uv run personal-context-mcp probe https://<your-address>/mcp
```

✅ It prints what the server answered at each step and ends with `All 7 checks passed.` That means
the apps can find the sign-in page and register themselves. If a line starts with `FAIL`, it says
which step broke; fix that before connecting any app.

## The custom instruction (same for every app)

Copy the text below and paste it into each app's custom instruction field:

```
You have access to Personal Context, my canonical cross-provider memory shared
across ChatGPT, Claude, Gemini and Grok.

When my request depends on past work (a project, earlier decisions, my lists,
"remember", "last time", a continuing task), call context_bootstrap once with a
short query of key terms you choose (names, projects, topics) and the project
if you know it. Never paste my whole message into it. If I refer to something
previously discussed, search Personal Context rather than assuming this app's
native memory is complete. Do not retrieve for standalone questions.

When durable state is established, checkpoint it: decisions, project state,
confirmed facts, tasks, list changes, unresolved questions and important
artifact relationships, naming the project when there is one. Do not save
conversational noise or duplicates.

Use my real lists through the list tools rather than writing out a list from
memory.

When meaningful files or generated artifacts should be retained, capture them
through Personal Context. Never tell me an artifact is archived unless its
bytes or complete text were actually stored.

Treat Personal Context as canonical shared state; this app's native memory is
supplemental. Preserve provenance and distinguish my direct statements from
model-generated analysis and external research.
```

This is the same text as [instructions/shared.md](../../instructions/shared.md). It's deliberately
short: the app reads it with every message. The detailed procedures live in Skills, tool descriptions
and MCP prompts, which are loaded only when needed.

## App by app

| App | Where to add it | Where the instruction goes | Needs | Full guide |
| --- | --- | --- | --- | --- |
| **Claude** | claude.ai → **Settings → Connectors → Add custom connector** | **Settings → Profile** → "What personal preferences should Claude consider" | any plan that offers custom connectors | [instructions/claude.md](../../instructions/claude.md) |
| **ChatGPT** | chatgpt.com → **Settings → Security and login → Developer mode** on, then **Settings → Plugins → +** | **Settings → Personalization → Custom instructions** | a plan with developer mode (Plus, Pro, Business, ...) | [instructions/chatgpt.md](../../instructions/chatgpt.md) |
| **Gemini** | gemini.google.com **on a computer** → **Settings & help → Connected Apps → Custom apps for Spark → Add a custom app** | **Settings & help → Saved info** | **Google AI Pro or Ultra** (not Plus), a *personal* Google account, Keep Activity on | [instructions/gemini.md](../../instructions/gemini.md) |
| **Grok** | **grok.com/connectors** (not your accounts.x.ai page) → **New Connector → Custom** | **Settings → Customize** | a paid Grok plan | [instructions/grok.md](../../instructions/grok.md) |

The menus in these apps move around every few months. If a menu name doesn't match, look for
"Connectors", "Apps" or "Integrations" in the settings, and check the full guide for the latest notes.

> **Same account everywhere.** Step 2's sign-in works with any Google account, but only the email(s)
> in `PC_ALLOWED_SUBJECTS` get in, and Gemini additionally refuses custom apps for work/school
> (Workspace) accounts. Use one **personal** Google account for all four apps, and make sure it's
> the one you gave the setup wizard.

## Recommended: turn off each app's built-in memory

Each app also keeps its own private memory of you. Left on, it competes with Personal Context: an
app may answer from its own stale notes instead of checking the shared record, and the four apps drift
apart. With it off, every app gets its memory from the same place.

**First, download your history** from each app ([guide 9](09-import-your-history.md), step 1), so
nothing is lost. Turning memory off doesn't delete your chats, but the export is what gets your past
conversations into Personal Context.

| App | Where | Turn **off** | Keep **on** |
| --- | --- | --- | --- |
| **Claude** | **Settings → Capabilities** (or **Settings → Memory**) | *Generate memory from chat history* and *Search and reference chats* | the Personal Context connector |
| **ChatGPT** | **Settings → Personalization → Memory** | *Reference saved memories* and *Reference chat history* | Custom instructions |
| **Gemini** | **Settings → Personal Intelligence** (older versions: **Personal context** or **Past chats**) | *Your past chats with Gemini* | **Gemini Apps Activity / Keep Activity**: custom apps stop working without it. Also keep Saved info, where your instruction lives. |
| **Grok** | **Settings → Customize** or **Data controls** | *Memory* / *Personalize with memories* | Custom instructions |

Optional: each app also has a switch that allows your chats to be used to train its models (Claude:
**Privacy → Help improve Claude**; ChatGPT: **Data controls → Improve the model for everyone**;
Grok: **Data controls → Improve the model**; Gemini: tied to Keep Activity, so leave it on). Turning
it off doesn't affect Personal Context.

Menu names change often. If one doesn't match, search the app's settings for "memory" or "past chats".

## Check that it worked

In each app, start a new chat and try:

> Remember that my test phrase is "purple giraffe".

The app should say it saved something, and some apps ask you to approve the first save. Then, in a
**different** app:

> What's my test phrase? Check my personal context.

✅ It answers "purple giraffe" and says where it found it. That's the whole point working: one app
learned it, another one knew it.

## What to expect day to day

- **Approvals.** Claude asks before the first use of each saving tool; ChatGPT confirms saves; Gemini
  asks every time it saves. You can usually choose "always allow" for the saving tools you trust.
- **Files you attach** in the apps are *seen* by the AI, but most apps don't hand the actual file to
  connectors. The app records that the file existed; import your chat export later
  ([guide 9](09-import-your-history.md)) to fill in the real file. Files the AI *creates*, like charts,
  code or documents, can be saved directly: just ask it to "save this to personal context".
- **Skills (Claude, and ChatGPT where available)** are optional add-ons with more detailed
  instructions. Upload each folder under [skills/](../../skills/) in the app's skills settings. The
  apps work without them.

## Common problems

| You see | Fix |
| --- | --- |
| The sign-in window says `redirect_uri_mismatch` | Your Google client's redirect URI doesn't match your address + `/auth/callback` ([guide 3](03-google-sign-in.md)). |
| Sign-in works, then *access denied* / *not allowed* | You chose a Google account that isn't in `PC_ALLOWED_SUBJECTS`. |
| ChatGPT has no *Apps & Connectors*, only *Plugins* | That's the newer layout. Turn on **Settings → Security and login → Developer mode**, then add the server under **Settings → Plugins → +**. |
| The connector was added but no tools appear | Run the check in [Check the server first](#check-the-server-first-1-minute). If it passes, remove the connector and add it again, or reload the page. ChatGPT especially sometimes needs this. |
| One app is missing some tools the others have (for example Gemini has no `note_write` or `review_list`) | `PC_CLIENT_SCOPE_POLICY` gives that app fewer permissions. An app that matches no entry gets read-only tools. Use `{"*":["*"]}` to give every app everything, push the settings, redeploy, then remove and re-add the connector. |
| Grok won't accept the address | Grok needs a paid plan; also make sure the address ends in `/mcp`. |
| Gemini has no *Custom apps for Spark* section | Custom apps need **Google AI Pro or Ultra**; Plus and free don't include them. Use [guide 11](11-gemini-without-custom-apps.md) instead. Also a personal (not work or school) Google account, Keep Activity on, a US English account, and a computer browser. |
| The app never uses it on its own | Check the custom instruction is saved, or just ask explicitly: "check my personal context". |

**Next:** [7. Keep your secrets in 1Password](07-keep-secrets-in-1password.md) (optional), or
[8. Set up your Obsidian notebook](08-obsidian.md).
