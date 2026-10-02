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