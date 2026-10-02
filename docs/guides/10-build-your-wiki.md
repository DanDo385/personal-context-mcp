# 10. Build your wiki: links and maps

After a while your Obsidian vault holds hundreds of notes. This step connects them:

- **Links:** each note gets a short *Related* list at the bottom, pointing to the notes that genuinely
  help you understand it. Obsidian then shows backlinks both ways and draws the graph.
- **Maps:** a *map* page per topic ("MCP", "Home renovation", "Investing") lists every note on that
  topic, grouped into sections: fundamentals, how-to, research, decisions and so on. Maps live in the
  `70-Atlas` folder.

It's careful by design. A note that mentions another note's exact title is linked straight away. For
everything else, TypeSafe's Jev is asked "would this link actually help?". Only confident answers are
applied; borderline ones wait for your review; clear "no"s are remembered so they're never paid for
twice. Without a TypeSafe key, only the exact-title links are made.

## Session indexes and the Home map

Imported conversations stay in the database. The vault gets four index notes, one per assistant,
plus maps that point at notes you already have:

```bash
uv run personal-context-mcp vault-fill
```

That command reads summaries already stored. It does not extract history and it does not ask Jev.
It writes:

- `70-Atlas/ChatGPT`, `Claude`, `Grok`, and `Gemini`. Each lists the newest 200 sessions (title,
  date, and the first line of the stored summary). A row links to a note only when that session
  already has one. The rest of the history is a single "N older" line. Each Gemini row is one
  takeout day.
- A Home map in `70-Atlas` linking those indexes, your lists, and your notes.
- One area map per project that already has a note. Those notes are placed on Home and on their
  project map directly.

It also links a note that names another note's exact title, then queues the vault writes (the same
work as `wiki render`). The Obsidian helper from [guide 8](08-obsidian.md) writes the files. Use
`wiki links` and `wiki mocs` later when you want Jev to judge further links and placements.

Frontmatter tags `type/project`, `type/list`, `type/moc`, and `type/session-index` are added as
those files are rendered, so the graph can be filtered. Tags you already set on a note stay.
There is no separate tags column.

## Step 1: Create some maps

Let it suggest maps from topics that came up in at least three imported conversations (this needs the
close-reading step from [guide 9](09-import-your-history.md)):

```bash
uv run personal-context-mcp wiki seed-mocs --min-mentions 3
```

and/or add your own:

```bash
uv run personal-context-mcp wiki add-moc "Home renovation" --description "Everything about the house project."
uv run personal-context-mcp wiki add-moc "Kitchen" --parent "Home renovation"
```

## Step 2: Find links and place notes on maps

```bash
uv run personal-context-mcp wiki links --limit 500
uv run personal-context-mcp wiki mocs --limit 500
```

✅ Each prints what it did: how many links were made from exact titles, how many were judged, and how
many were accepted, sent to review or rejected.

## Step 3: Send it to Obsidian

```bash
uv run personal-context-mcp wiki render
```

This queues every note and map that changed. The Obsidian helper ([guide 8](08-obsidian.md)) writes
them within a minute. Your own writing in a note is never touched; only the generated section at the
bottom changes.

## Step 4: Check the health of your wiki

```bash
uv run personal-context-mcp wiki doctor
```

It reports notes with no links at all ("orphans", with a few examples), notes on no map, empty maps,
and how many decisions are waiting for review. Look at those with:

```bash
uv run personal-context-mcp review
```

## Run it again whenever you like

All four commands only do new work: notes already linked and placed are skipped unless you add
`--redo`. A comfortable rhythm is weekly, or after a big import.

## Tuning (advanced)

The confidence needed to act is set in
[`src/personal_context_mcp/classifier/questions.py`](../../src/personal_context_mcp/classifier/questions.py):

| Setting | Default | Meaning |
| --- | --- | --- |
| `WIKI_LINK_AUTO` / `WIKI_LINK_NAVIGATION_AUTO` | 0.90 / 0.80 | Link automatically at or above both |
| `WIKI_LINK_REVIEW` | 0.65 | Between this and the above: ask you |
| `MOC_AUTO` / `MOC_REVIEW` | 0.85 / 0.60 | The same, for placing a note on a map |

Start with the defaults, look at 30–50 decisions in `review`, and lower the thresholds if nearly all of
them were right.
