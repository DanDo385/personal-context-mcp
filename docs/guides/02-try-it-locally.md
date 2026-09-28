# 2. Try it on your computer (optional)

This runs the whole thing on your own computer for ten minutes, before you create any accounts. It's
optional, but it's the quickest way to see that everything works. You'll throw this setup away later
(or keep it for experimenting).

The phone apps and the ChatGPT, Claude, Gemini and Grok websites **can't** reach a server on your
computer. To test, you'll use Claude Code or a small test tool instead.

## Step 1: Start a database

**With Docker Desktop** (open the Docker Desktop app first, so it's running):

```bash
docker compose up -d db
```

✅ You should see `Container personal-context-mcp-db-1  Started`. The database is now running in the
background and keeps running until you stop it with `docker compose down`.

**Without Docker:** do [guide 4](04-database.md) first to create a free Supabase database, and use its
connection string in the next step.

## Step 2: Write your settings

```bash
uv run personal-context-mcp init
```

It asks a few questions. For this test:

| Question | Answer |
| --- | --- |
| Where will it run? | `local` |
| Which email...? | your email (anything works for a local test) |
| Connection string | press **Enter** if you used Docker; otherwise paste your Supabase one |
| TypeSafe key / Anthropic key | press **Enter** to skip |
| Obsidian vault | press **Enter** |

✅ It prints `Wrote .env.local (readable only by you)` and **a long test access token**. Copy the token
somewhere for a few minutes; you'll need it in step 5. It's also saved in `.env.local` (the line starting
`PC_STATIC_TOKENS`).

## Step 3: Create the tables and check everything

```bash
uv run personal-context-mcp migrate
uv run personal-context-mcp check
```

✅ The last line should say **All checks passed.**

## Step 4: Start the server

```bash
uv run personal-context-mcp serve
```

✅ You'll see some log lines ending with `Uvicorn running on http://0.0.0.0:8080`. Leave this
Terminal window open; the server runs as long as it's open. Press Ctrl-C to stop it.

In a **new** Terminal window, check it answers:

```bash
curl http://localhost:8080/healthz
```

✅ It prints `ok`. That means it's alive.

## Step 5: Talk to it

### Option A: Claude Code (if you have it)

Replace `YOUR-TOKEN` with the token from step 2:

```bash
claude mcp add --transport http personal-context http://localhost:8080/mcp \
  --header "Authorization: Bearer YOUR-TOKEN"
```

Start `claude`, type `/mcp`, and check that **personal-context** shows as connected. Then try:

> Remember that my favourite editor is Obsidian.

and, in a brand-new Claude Code session:

> What's my favourite editor? Check personal context.

✅ It finds the answer in your server, not in its own memory.

### Option B: MCP Inspector (a test tool; needs Node.js from guide 1)

```bash
npx @modelcontextprotocol/inspector
```

A browser tab opens. Set **Transport** to *Streamable HTTP* and **URL** to
`http://localhost:8080/mcp`. Under **Authentication**, add the header `Authorization` with the value
`Bearer YOUR-TOKEN`. Click **Connect**, then **List Tools**.

✅ You'll see 48 tools. Try `memory_save` and then `memory_search`.

## What to do next

- Happy? Stop the server (Ctrl-C) and go on to [3. Create your Google sign-in](03-google-sign-in.md).
  When you run `init` again for the online setup, it will refuse to overwrite this `.env.local`. Either
  rename the old one (`mv .env.local .env.local-test`) or pass `--force`.
- Something went wrong? See [Troubleshooting](../troubleshooting.md). The most common issue is that
  Docker Desktop isn't running (`Cannot connect to the Docker daemon`).
