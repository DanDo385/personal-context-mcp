# 8. Set up your Obsidian notebook

[Obsidian](https://obsidian.md) is a free notes app that works on plain files in a folder (a
*vault*). personal-context-mcp fills a vault with clean, linked notes: research, project pages, your
lists, and "map" pages for your topics. You can read them, edit them, and see how they link together.

The notes are written by **the helper** (`obsidian-sync`), a small program on your computer. It asks
your server "anything new to write?" every 30 seconds. Nothing on the internet can reach into your
computer: the helper only ever calls out.

## Step 1: Choose where the vault lives

**Want your notes on your iPhone or iPad too?** (Recommended.) The Obsidian phone app can only open
vaults kept in iCloud Drive's Obsidian folder, which has an awkward address. Make a short shortcut to
it once, so everything else can use `~/Obsidian`:

```bash
ln -s ~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents ~/Obsidian
```

> **Tip: use Tab to fill the path.** Type `ln -s ~/Library/Mobile` then press **Tab**, and your terminal
> fills in the rest. If it doesn't, you haven't installed Obsidian on this Mac yet, or iCloud Drive
> isn't set up.

(Obsidian must be installed on the Mac for that iCloud folder to exist. If `~/Obsidian` already exists,
skip the shortcut and, in the next step, use the full address instead:
`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Personal Context`.) Your notes are stored in iCloud and show up
in Obsidian on your phone; `~/Obsidian/Personal Context` is just an easier way to refer to them.

**Mac only?** Skip the shortcut. `~/Obsidian/Personal Context` will then be an ordinary local folder.

Open your `.env.local` and set:

```
OBSIDIAN_SYNC_VAULT_PATH=~/Obsidian/Personal Context
```

(the setup wizard already added this line, and the matching `OBSIDIAN_SYNC_API_URL` and
`OBSIDIAN_SYNC_API_KEY`). Create the folder if it doesn't exist:

```bash
mkdir -p ~/Obsidian/"Personal Context"
```

Then in Obsidian: **Open folder as vault** and pick that folder.

## Step 2: Test the helper once

```bash
uv run obsidian-sync run --once
```

✅ It exits without errors. If your AI apps have saved notes already, they appear in the vault. If
not, nothing is written yet, and that's fine.

To see what it *would* write without writing anything: `uv run obsidian-sync run --once --dry-run`.

## Step 3: Keep it running in the background

**Mac:**

```bash
deploy/install-worker.sh
```

✅ It prints `Installed ...` and where its log is. From now on it starts when you log in and restarts
if it ever stops. To stop it for good:
`launchctl bootout gui/$(id -u)/com.personal-context-mcp.obsidian-sync`.

**Linux (systemd):**

```bash
mkdir -p ~/.config/systemd/user
cp deploy/obsidian-sync.service ~/.config/systemd/user/
cp deploy/obsidian-sync.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now obsidian-sync.timer
```

✅ Check it's running: `systemctl --user status obsidian-sync`. To see recent writes:
`journalctl --user -u obsidian-sync -n 20`.

The timer runs the helper every 5 minutes. To stop: `systemctl --user disable --now obsidian-sync.timer`.
Logs: `journalctl --user -u obsidian-sync -f`.

(Tip: if `systemctl --user` says "Failed to connect to bus", your Linux setup doesn't have a user
session manager. Use `systemctl` with `sudo` instead: copy the files to `/etc/systemd/system/` and
replace `%h` in the service file with your full home directory path.)

**Windows (WSL):** run `uv run obsidian-sync run` in a terminal you leave open.

## What you'll find in the vault

| Folder | What goes there |
| --- | --- |
| `00-Inbox` | Things worth keeping whose home isn't clear yet |
| `20-Projects` | One folder per project: status page, lists, saved files |
| `30-Research` | Findings from research conversations, with sources |
| `40-Wiki` | Explanations of how things work |
| `50-Concepts` | Single ideas and terms |
| `60-Resources` | Tools, articles, your lists, saved files not tied to a project |
| `70-Atlas` | One session index per assistant, plus map pages that link a topic ([guide 10](10-build-your-wiki.md)) |

The four index notes are `ChatGPT`, `Claude`, `Grok`, and `Gemini`. Each lists the newest
200 sessions: title, date, and the first line of the summary already stored. A row is a link
only when that session already has its own note. Older sessions are one line, "N older".
Gemini rows are takeout days (one row per day in the export). `vault-fill` writes these
from the database. It does not call a model. See [guide 10](10-build-your-wiki.md).

Lists render open, blocked, and done items. Dropped items stay out of the file.

Rendered files carry a `type/` tag in frontmatter so the graph can be filtered:
`type/project` on project notes, `type/list` on lists, `type/moc` on maps, and
`type/session-index` on the four indexes. Tags already stored on a note are kept.

## Editing notes

You can edit any note. The helper notices and sends your change back to your server, so every AI app
sees your version. Two things to know:

- The bottom of a note, between the lines `pc:generated` and `/pc:generated`, is maintained
  automatically (links, maps, sources). Anything you type there is replaced; write above it.
- If you edit a note at the same moment an AI app updates it, your edit wins. The update is held back
  as a *conflict* instead of overwriting you.

Ticking a checkbox in a list note (`- [x]`) marks that item done everywhere.

## Common problems

| You see | Fix |
| --- | --- |
| `Vault directory does not exist` | Create the folder (step 1), or fix `OBSIDIAN_SYNC_VAULT_PATH`. |
| `worker rejected: check OBSIDIAN_SYNC_API_KEY matches` | The helper's key differs from the server's `PC_WORKER_API_KEY`. They must be identical; after changing the server's, run `deploy/vercel-env.sh .env.local` and `vercel deploy --prod`. |
| Notes never appear | Your AI apps may not have saved any notes yet. Ask one to "save this research to Obsidian". Also check the log: `tail -f ~/Library/Logs/personal-context-mcp/obsidian-sync.err.log`. |

**Next:** [9. Import your old chats](09-import-your-history.md)
