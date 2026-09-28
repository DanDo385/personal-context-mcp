# 8. Set up your Obsidian notebook

[Obsidian](https://obsidian.md) is a free notes app that works on plain files in a folder (a
*vault*). personal-context-mcp fills a vault with clean, linked notes: research, project pages, your
lists, and "map" pages for your topics. You can read them, edit them, and see how they link together.

The notes are written by **the helper** (`obsidian-sync`), a small program on your computer. It asks
your server "anything new to write?" every 30 seconds. Nothing on the internet can reach into your
computer: the helper only ever calls out.

## Step 1: Choose where the vault lives

- **To see notes on your iPhone or iPad too:** put the vault in iCloud Drive's Obsidian folder:
  `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Personal Context`
  (Obsidian must be installed on the Mac for that folder to exist.)
- **Otherwise:** anywhere, for example `~/Obsidian/Personal Context`.

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

**Linux (systemd):** create `~/.config/systemd/user/obsidian-sync.service`:

```ini
[Unit]
Description=personal-context-mcp Obsidian helper

[Service]
WorkingDirectory=%h/personal-context-mcp
ExecStart=%h/personal-context-mcp/.venv/bin/obsidian-sync run
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

then `systemctl --user enable --now obsidian-sync`.

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
| `70-Atlas` | Map pages linking everything on a topic ([guide 10](10-build-your-wiki.md)) |

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
