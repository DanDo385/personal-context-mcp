# 9. Import your old chats

Years of conversations in ChatGPT, Claude, Gemini and Grok can become searchable, shared memory. This
guide downloads your history from each app, imports it, and then (optionally) has the important
conversations read closely and turned into proper notes.

**Nothing here changes or deletes anything in the apps themselves.** Importing only reads the files
you download, and running an import twice never creates duplicates.

## How it works

```
 your export files ──► 1. saved exactly as they are (every message kept)
                       2. privacy screen, on your computer
                       3. quick sorting pass ("which project? worth keeping?")      ← cheap
                       4. close reading of the conversations worth it               ← optional, costs more
                       5. decisions, facts and notes your apps can use
```

- **Step 2, the privacy screen,** runs on your computer before anything is sent anywhere.
  Conversations containing passwords, API keys, private keys, ID numbers or card numbers are marked
  *highly sensitive* and never leave your computer. Health, legal and money conversations are marked
  *private*.
- **Step 3** uses TypeSafe's Jev if you added a TypeSafe key, or simple built-in rules if you didn't.
  It only sees a short excerpt (opening and closing messages) with emails and keys removed.
- **Step 4** uses Claude, if you added an Anthropic key, and only runs when you ask.

## Step 1: Download your exports

Each app emails you a download link, usually within minutes but sometimes up to a day. Save the `.zip`
files in an `exports` folder inside this project (Git ignores that folder).

| App | Where | What to download |
| --- | --- | --- |
| **ChatGPT** | chatgpt.com → **Settings → Data controls → Export data** | the `.zip` from the email |
| **Claude** | claude.ai → **Settings → Privacy → Export data** | the `.zip` from the email |
| **Gemini** | [takeout.google.com](https://takeout.google.com) → **Deselect all** → tick **My Activity** → **All activity data included** → choose only **Gemini Apps** → **Multiple formats** → set *Activity records* to **JSON** | the Takeout `.zip` |
| **Grok** | grok.com → **Settings → Data controls** → download/export your data | the `.zip` |

> **Gemini:** the JSON setting matters. Takeout's default is HTML, which can't be imported.

What the exports *don't* include (so it's not a bug): ChatGPT and Claude exports include text and
some files. Gemini's has no images or generated files and no conversation grouping, so conversations
are grouped by day. Grok's may lack generated images. Files an export only *names* are recorded as
"pending", never pretended to be saved.

## Step 2: Choose your privacy level

Open `.env.local` and decide (both lines can be left out, which uses the defaults shown):

```
# Every imported chat starts at least this private. The screen can only raise it.
#   normal           - default
#   highly_sensitive - keep your ENTIRE history on your computer (sorting uses built-in rules only)
PC_IMPORT_DEFAULT_SENSITIVITY=normal

# Which levels may never be sent to outside services (TypeSafe, Anthropic, embeddings).
# Add "private" to also keep health, legal and money conversations local.
PC_NO_EXTERNAL_SENSITIVITY=highly_sensitive
```

## Step 3: A small trial

Import 50 conversations first, to see what happens:

```bash
uv run personal-context-mcp import chatgpt exports/chatgpt-export.zip --limit 50
```

(Use `claude`, `gemini` or `grok` for the others, or `auto` to let it detect which app the file came
from.)

✅ It prints a report: how many conversations and messages were imported, how many were screened as
sensitive, how many files were saved or only named, and how many items need your review. Then:

```bash
uv run personal-context-mcp review
```

lists anything the sorting pass wasn't sure about ("which project does this belong to?").

## Step 4: Import everything

```bash
uv run personal-context-mcp import chatgpt exports/chatgpt-export.zip
uv run personal-context-mcp import claude  exports/claude-export.zip
uv run personal-context-mcp import gemini  exports/takeout.zip
uv run personal-context-mcp import grok    exports/grok-export.zip
```

A few thousand conversations take a few minutes to an hour, depending mostly on the sorting pass.
You can stop with Ctrl-C and run the same command again later; it carries on without duplicating
anything.

## Step 5 (optional): Close reading of the important ones

The sorting pass flags conversations that are worth reading closely: ones with decisions, findings or
plans. This step sends only those, never *highly sensitive* ones, to Claude, which writes a proper
summary and pulls out decisions, facts, open questions and topics.

It costs money, so **always look at the estimate first**:

```bash
uv run personal-context-mcp extract --dry-run --limit 500
```

✅ It shows how many conversations qualify, the estimated tokens, an estimated cost in US dollars, and
the titles of the first twenty. When you're happy:

```bash
uv run personal-context-mcp extract --limit 50        # start small, then raise the limit
```

To reduce cost, set `PC_EXTRACTION_MODEL=claude-sonnet-5` in `.env.local` (cheaper, still good), or raise
`--min-importance` (default 0.5) to only read the most important conversations.

Old to-dos found in old chats are kept as context ("Unfinished at the time: ...") rather than added to
your task lists, because most of them are long done.

## Later: re-sort after changes

If you create projects after importing (so old chats can be matched to them), or when a new version
improves the sorting questions:

```bash
uv run personal-context-mcp reclassify-conversations --dry-run
uv run personal-context-mcp reclassify-conversations
```

This works from what's already in your database. You don't need the export files again, and anything
the close reading wrote is kept.

## Common problems

| You see | Fix |
| --- | --- |
| `Could not identify which provider produced this archive` | Name the app explicitly: `import chatgpt file.zip`. For Gemini, check you chose JSON (step 1). |
| `This exact archive has already been imported` | Nothing to do; it's safe. |
| `extract` says *Set PC_EXTRACTION_PROVIDER=anthropic* | Add `PC_EXTRACTION_PROVIDER=anthropic` and your `PC_ANTHROPIC_API_KEY` to `.env.local`. |
| Lots of *classifier_degraded* in the report | TypeSafe was unreachable; the built-in rules were used instead and those conversations are queued. Run `uv run personal-context-mcp reclassify` later. |

**Next:** [10. Build your wiki](10-build-your-wiki.md)
