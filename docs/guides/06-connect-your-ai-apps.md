# 6. Connect ChatGPT, Claude, Gemini and Grok

Each app is connected **once, on a computer, in its website**. After that the connection also works in
the app's phone version. Every app follows the same three steps:

1. **Add a connector** with your server's address followed by `/mcp`, for example
   `https://personal-context-mcp.vercel.app/mcp`.
2. **Sign in with Google** in the window that opens, choosing the account whose email you gave the
   setup wizard.
3. **Paste the custom instruction** so the app knows when to use its new memory.

## The custom instruction (same for every app)

Copy the text inside the box in [instructions/shared.md](../../instructions/shared.md). It's short on
purpose, because the app reads it with every message. It tells the app to look things up before
answering questions about your past work, and to save what gets decided.

## App by app

| App | Where to add it | Where the instruction goes | Needs | Full guide |
| --- | --- | --- | --- | --- |
| **Claude** | claude.ai → **Settings → Connectors → Add custom connector** | **Settings → Profile** → "What personal preferences should Claude consider" | any plan that offers custom connectors | [instructions/claude.md](../../instructions/claude.md) |
| **ChatGPT** | chatgpt.com → **Settings → Apps & Connectors → Advanced → Developer mode**, then **Create** | **Settings → Personalization → Custom instructions** | a plan with developer mode | [instructions/chatgpt.md](../../instructions/chatgpt.md) |
| **Gemini** | gemini.google.com **on a computer** → **Settings & help → Connected Apps → Custom apps → Add** | **Settings & help → Saved info** | a *personal* Google account with Keep Activity on | [instructions/gemini.md](../../instructions/gemini.md) |
| **Grok** | grok.com/connectors → **New Connector → Custom** | **Settings → Customize** | a paid Grok plan | [instructions/grok.md](../../instructions/grok.md) |

The menus in these apps move around every few months. If a menu name doesn't match, look for
"Connectors", "Apps" or "Integrations" in the settings, and check the full guide for the latest notes.

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
| **Gemini** | **Settings & help → Personal context** (sometimes **Past chats**) | *Your past chats with Gemini* | **Gemini Apps Activity / Keep Activity**: custom apps stop working without it. Also keep Saved info, where your instruction lives. |
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
| The connector was added but no tools appear | Remove it and add it again, or reload the page. ChatGPT especially sometimes needs this. |
| Grok won't accept the address | Grok needs a paid plan; also make sure the address ends in `/mcp`. |
| Gemini has no *Custom apps* option | It needs a personal (not work or school) Google account, Keep Activity switched on, and a computer browser. |
| The app never uses it on its own | Check the custom instruction is saved, or just ask explicitly: "check my personal context". |

**Next:** [7. Keep your secrets in 1Password](07-keep-secrets-in-1password.md) (optional), or
[8. Set up your Obsidian notebook](08-obsidian.md).
