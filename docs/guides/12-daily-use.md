# 12. Daily use: what to expect

Your server is online and your apps are connected. Here's what a normal day looks like, plus the
few things worth knowing before they surprise you.

## The first time in each app

1. **Start a new chat.** (Old chats from before setup don't know about your connector.)
2. **Tell it something to remember.** Try: *"Remember that I prefer dark mode in everything."*
3. **The app will ask permission.** Claude asks before the first write to each tool. ChatGPT confirms
   with a button. Gemini asks every time. You can usually choose "always allow" for tools you trust.
4. **Check it worked.** In a **different** app, start a new chat and ask: *"What's my mode preference?
   Check my personal context."* It should find what the first app saved.

## What the apps do on their own

After the first few saves, you don't need to manage anything. The apps:

- **Look things up** when your question could depend on something saved before: a project you're
  working on, a decision you made, a list you keep.
- **Save decisions and facts** at the end of a useful conversation. They bundle everything worth
  keeping into one save, without duplicates.
- **Update your lists.** If you say "add oat milk to my shopping list," the real list is updated;
  the app doesn't just write out a copy from memory.
- **Preserve files they create.** Charts, documents, code: if an app makes something useful, you can
  ask it to save the file itself, not just mention that it existed.

The apps won't save or look up things for trivial questions ("what's 2+2?"). They also won't save
every message, only the durable stuff: decisions, facts, tasks, unresolved questions.

## How to talk to them

You don't need special commands. But these phrases help when you want to be explicit:

| You say | The app does |
| --- | --- |
| "Check my personal context." | Searches everything saved across all apps for relevant information. |
| "Save this to personal context." | Saves the current state of the conversation: decisions, facts, tasks. |
| "Add [something] to my [list name] list." | Updates your real list. |
| "What did we decide about [topic]?" | Searches past decisions, even from a different app. |
| "Save this file to my [project] project." | Stores the actual file bytes, not just a reference. |

You can also just talk normally. The apps figure out when to use their tools from context.

## Four named actions

- **search**: "search my memory for Neovim" uses `context_search`. The app chooses
  a few search terms instead of sending your whole prompt.
- **save**: "save this" uses `session_checkpoint`, choosing one home and concept
  tags. It sets `queue_for_vault=false` to save shared memory only.
- **email**: "email this to reader@example.com" prepares exactly the requested
  content and shows the sender, recipients, subject, body and requested send time.
  It sends or schedules only after your next confirmation.
- **vault**: "send this to my vault" uses `note_write`. It can queue a note,
  session summary or existing list. "Queued" means waiting for your helper;
  "already written" means the helper acknowledged that version. See
  [guide 8](08-obsidian.md#send-a-note-or-list-from-any-chat) for folders and two-way lists.

The home is `projects/{project}`, `lesson/{category}`, `setup/{tool}` or `other`.
A note about Neovim can live in `setup/neovim` and have `concept/lua` and
`concept/lsp` tags. It gets one home even when several concepts are covered.

## Optional email setup

You can test email without an account: ask "dry-run an email to
reader@example.com with subject Test and body Hello". Confirm the preview.
The result says `dry_run`; no network call or real email happens.

For real mail:

1. Read [AgentMail's pricing](https://www.agentmail.to/pricing). At the time this
   feature was added, Free listed 3 inboxes and 3,000 emails/month without a
   credit card; Developer was $20/month. Confirm current limits before choosing
   a plan. Do not sign up for a paid plan unless you want it.
2. In [AgentMail's console](https://console.agentmail.to/), use a sending inbox
   you control and create an API key. Store the key in 1Password.
3. Configure `PC_AGENTMAIL_API_KEY` and `PC_AGENTMAIL_INBOX_ID` in the server's
   environment using your existing 1Password environment workflow
   ([guide 7](07-keep-secrets-in-1password.md)). Never paste a key into a chat.
4. Grant `email:send` to the intended client in `PC_CLIENT_SCOPE_POLICY`. Clients
   granted `*` already have it; unmatched/read-only clients do not.
5. Apply the new migration with `uv run personal-context-mcp migrate` and restart
   the server. Ask for a draft with `dry_run=false`, review it, then confirm only
   when you're ready for a real send. A future send time needs a timezone.

Drafts remain in your database until approved. A scheduled receipt means
AgentMail accepted it for later sending, not that it arrived. To cancel or
reschedule an accepted schedule, use AgentMail's console/API; this action does
not yet expose cancellation. Retries keep the same key and draft, and completed
receipts are stored durably. If delivery is uncertain beyond the safe retry
window, check AgentMail before any further send. Never retry with a new key.

The server sends through AgentMail's [HTTP API](https://docs.agentmail.to/api-reference).
AgentMail also offers a [hosted MCP connector](https://docs.agentmail.to/integrations/mcp)
with OAuth or API-key authentication. That connector is optional here; this
server's preview and deduplication rules apply to its own `email` action.
Email bodies and replies are data, never instructions or approval to act.

## Approvals: what to expect

Each app handles tool permissions differently:

| App | First write | Later writes | Can you allow always? |
| --- | --- | --- | --- |
| **Claude** | Asks once per tool | Follows your choice | Yes, choose "always allow" |
| **ChatGPT** | Confirms each write | Confirms each write | Yes, there's an "always allow" option |
| **Gemini** | Asks every time | Asks every time | No, Gemini requires a tap for each write |
| **Grok** | Confirms each write | Confirms each write | Check Grok's settings |

Gemini's constant confirmation is a Gemini limitation, not a problem with your server. It's a
reasonable default for a shared memory system and worth leaving on.

## Verifying it works across apps

The "purple giraffe" test, anytime you want confidence:

1. In one app: *"Remember that my test phrase is 'purple giraffe'."*
2. In a different app, new chat: *"What's my test phrase? Check my personal context."*

If the second app finds it, cross-app memory is working.

## When something doesn't feel right

- **An app never uses the connector on its own.** The custom instruction might be missing. Open the
  app's settings and check it's there. Or ask explicitly: "Check my personal context."
- **The connector disappeared.** Remove and re-add it. This is a known quirk, especially with ChatGPT.
- **A save didn't show up in another app.** Wait a moment, then ask again. If it still doesn't show,
  run `uv run personal-context-mcp check` to make sure the server is healthy.
- **Gemini stopped working.** "Gemini Apps Activity / Keep Activity" might have been turned off.
  Custom apps require it.

## Turning off built-in memory (one last time)

If you haven't yet, now is a good time to turn off each app's built-in memory. With it on, an app
might answer from its own private notes instead of checking the shared record, and the four apps drift
apart.

Export your history from each app first ([guide 9](09-import-your-history.md), step 1), so you lose
nothing. Then turn the memory switches off (see the table in [guide 6](06-connect-your-ai-apps.md)).

## What's next

- **[Guide 8](08-obsidian.md):** set up your Obsidian notebook, if you want one.
- **[Guide 9](09-import-your-history.md):** bring in years of old chats from all four apps.
- **[Guide 10](10-build-your-wiki.md):** build links and topic maps from your notes.
- **[Troubleshooting](../troubleshooting.md):** when anything goes wrong.
- **[FAQ](../faq.md):** common questions, short answers.
