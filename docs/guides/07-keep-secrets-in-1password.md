# 7. Keep your secrets in 1Password (optional)

> **You can skip this entire guide.** The `.env.local` file on your computer is already readable only
> by your user account, and it's never uploaded to GitHub. 1Password adds an extra layer but changes
> nothing about how the project works. If you're happy with `.env.local`, go straight to
> [guide 8](08-obsidian.md).

The setup wizard keeps your secrets in a `.env.local` file on your computer, readable only by your user.
That's fine for most people. If you use [1Password](https://1password.com), you can go one better: no
secret ever sits in a file, and every secret is in the same place as your other passwords.

There are two ways. **Choose one.**

| | A. Environments (recommended) | B. Secret references (`op://`) |
| --- | --- | --- |
| How it works | 1Password pretends to be your `.env.local` file. The project reads it as usual, but nothing is stored on disk. | Files list *where* each secret lives in 1Password; the `op` command fills them in when a command runs. |
| Setup effort | Low: a few clicks, or ask Claude Code to do it | Medium |
| Works when 1Password is locked | No | Yes, with a service account |
| Platforms | Mac, Linux | Mac, Linux, Windows (WSL) |

## A. 1Password Environments (recommended)

A 1Password *Environment* is a set of settings for one project. 1Password can "mount" it at the path of
your `.env.local` file. The file then reads normally, but its contents come straight from 1Password and are
never written to your disk.

### With Claude Code (easiest)

1. Install the 1Password plugin in Claude Code (from inside `claude`):

   ```
   /plugin marketplace add 1Password/1password-claude-plugin
   /plugin install 1password@1password
   ```

2. In the 1Password desktop app, open **Settings → Labs**, turn on **Enable local MCP server**, then
   **Settings → Developer → Integrate with MCP clients**. Restart Claude Code and check `/mcp` shows
   **1Password** as connected.
3. In this project folder, ask Claude Code:

   > Import .env.local into a 1Password Environment called personal-context-mcp and mount it here.

   1Password asks you to approve. Claude creates the Environment, marks keys and passwords as concealed,
   and mounts it at `.env.local`. The plugin never shows secret values to the AI, only the variable names.

### By hand

1. In the 1Password desktop app: **Developer → Environments → New environment**, name it
   `personal-context-mcp`.
2. **Import** your existing `.env.local` file (or add each variable).
3. **Destinations → Local .env.local file**, and choose this project's `.env.local` path.
4. Delete the old plaintext copy if 1Password didn't replace it.

### Afterwards

Everything keeps working unchanged (`uv run personal-context-mcp ...`, `deploy/vercel-env.sh .env.local`),
because the project still reads `.env.local`. 1Password may ask you to approve access the first time.

> **Rotate what used to be in plain text.** Moving secrets into 1Password protects them from now on,
> but a copy may still exist in backups. When convenient, create a new Google client secret, a new
> Supabase database password and new API keys, and update them in 1Password.

## B. Secret references (`op://` files)

Every value in 1Password has an address like `op://Private/Personal Context/Server/worker_api_key`.
Instead of secrets, your files hold these addresses, and the
[1Password CLI](https://developer.1password.com/docs/cli/get-started/) (`op`) swaps in the real values
only while a command runs.

1. Install and sign in to `op` (its getting-started guide takes five minutes). Check with `op whoami`.
2. Create one 1Password item, for example *Personal Context*, with three sections, `Server`,
   `Google OAuth` and `Supabase`, holding the values from your `.env.local`. Tip: reference the item by its
   **id** (from `op item get "Personal Context" --format json`), so renaming it never breaks anything.
3. Copy the templates and replace `<vault>` and `<item-id>`:

   ```bash
   cp deploy/production.env.op.example deploy/production.env.op   # what Vercel gets
   cp deploy/admin.env.op.example      deploy/admin.env.op        # commands you run
   cp deploy/worker.env.op.example     deploy/worker.env.op       # the Obsidian helper
   ```

   These copies are ignored by Git, so they can't be committed by accident.
4. Optional: create `deploy/local.env` with `PC_OP_VAULT=...` and `PC_OP_ITEM=<item-id>`, so
   `deploy/preflight.sh` can warn you about any field still left empty.
5. Use them:

   ```bash
   op run --env-file=deploy/admin.env.op -- uv run personal-context-mcp migrate
   deploy/vercel-env.sh                  # picks up deploy/production.env.op automatically
   deploy/install-worker.sh              # picks up deploy/worker.env.op automatically
   ```

**For background jobs** (like the Obsidian helper starting at login), `op` needs to work without you
there to approve it. Create a
[1Password service account](https://developer.1password.com/docs/service-accounts/get-started/) with
read access to just the vault holding this item, and make its token available as
`OP_SERVICE_ACCOUNT_TOKEN` in your shell profile. Treat that token like a password.

**Next:** [8. Set up your Obsidian notebook](08-obsidian.md)
