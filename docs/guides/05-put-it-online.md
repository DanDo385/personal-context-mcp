# 5. Put it online

Now your server goes on the internet so the AI apps can reach it from anywhere, including your phone.
The guides use [Vercel](https://vercel.com). Its free *Hobby* plan is meant for personal projects like
this one. When you're done you'll have an address like `https://personal-context-mcp.vercel.app`.

Before starting, you should have:

- a Google Client ID and secret ([guide 3](03-google-sign-in.md))
- a Supabase connection string, project URL and service key ([guide 4](04-database.md))

## Step 1: Write your online settings

If you haven't already done this in guide 4:

```bash
uv run personal-context-mcp init
```

Choose **online**. When it asks for your address, use the one you'll have after step 3, for example
`https://personal-context-mcp.vercel.app`. (If your `.env.local` from the local test is in the way, rename
it first with `mv .env.local .env.local-test`.)

✅ Then:

```bash
uv run personal-context-mcp migrate
uv run personal-context-mcp check
```

should end with **All checks passed.**

## Step 2: Sign in to Vercel

Create a free account at <https://vercel.com/signup> (signing up with GitHub is easiest), then:

```bash
vercel login
```

✅ It says `Congratulations! You are now signed in.`

## Step 3: Create your Vercel project

```bash
vercel project add personal-context-mcp
vercel link --yes --project personal-context-mcp
```

✅ It says `Linked to <your-name>/personal-context-mcp`. The name decides your free address: a project
called `personal-context-mcp` gets `https://personal-context-mcp.vercel.app` if that name is free.
If it's taken, Vercel adds a suffix; check the real address under **Domains** in the project's page on
vercel.com. Then make sure `PC_BASE_URL` in `.env.local` and the redirect URI in Google (guide 3) use that
exact address.

This project already tells Vercel how to run it (`vercel.json` and `pyproject.toml`), so there's
nothing to configure on the website.

> **Shortcut:** once steps 1-3 are done, `deploy/go-live.sh .env.local` does steps 4 and 5 for you,
> including the checks, and prints the address to use in your AI apps.

## Step 4: Give Vercel your settings

```bash
deploy/vercel-env.sh .env.local
```

It copies each setting from your `.env.local` into Vercel's *Production* environment, one at a time, marking
passwords and keys as *sensitive* so the Vercel website never shows them. (Settings for the Obsidian
helper stay on your computer.)

✅ You'll see one `set PC_...` line per setting, then `Done`.

## Step 5: Deploy

```bash
vercel deploy --prod
```

It uploads the code, installs everything and starts your server. That takes about a minute.

✅ It ends with your production address. Now open **a private/incognito browser window** and visit:

```
https://personal-context-mcp.vercel.app/healthz
```

(use your address). You should see `ok`.

**If you see a Vercel login page instead of `ok`:** Vercel is protecting the address with its own
login, which would also block ChatGPT and the other apps. Your server already has its own sign-in
(Google, limited to your email), so turn Vercel's off for this project:

```bash
vercel project protection disable personal-context-mcp --sso
```

Then check `/healthz` again.

## Step 6 (optional): Use your own domain

If you own a domain, you can use an address like `https://memory.yourname.com`:

1. `vercel domains add memory.yourname.com personal-context-mcp`
2. At your domain's DNS provider, add a **CNAME** record: name `memory`, value `cname.vercel-dns.com`.
   On Cloudflare, set it to **DNS only** (grey cloud): Cloudflare's proxy can break the live
   connection the AI apps use.
3. Change `PC_BASE_URL` and `PC_ALLOWED_HOSTS` in `.env.local` to the new address, run
   `deploy/vercel-env.sh .env.local` again, and add `https://memory.yourname.com/auth/callback` to your
   Google client's redirect URIs.
4. `vercel deploy --prod`.

> Use a sub-domain (`memory.yourname.com`), not a path (`yourname.com/memory`). The AI apps look for
> the sign-in details at the root of the address, so a path would send them to your other website.

## Updating later

When a new version of this project comes out:

```bash
git pull
uv sync
uv run personal-context-mcp migrate     # applies any new database changes
vercel deploy --prod
```

`migrate` is always safe to run: it only applies changes that haven't been applied yet.

## Other places to run it

Vercel is the simplest option, not the only one. The server is an ordinary Python web app, and the
project includes a `Dockerfile`, so it runs on Google Cloud Run, Fly.io, Railway, a home server, or
anything else that runs containers. [Architecture: Deployment](../architecture.md#deployment) covers
Cloud Run. Whatever you choose, it needs a public **https** address.

## Common problems

| You see | Fix |
| --- | --- |
| `/healthz` shows a Vercel login page | Step 5: `vercel project protection disable personal-context-mcp --sso`. |
| `/healthz` shows `500` or *This Serverless Function has crashed* | A setting is missing or wrong. Run `uv run personal-context-mcp check` locally, which lists every problem at once; fix `.env.local`, re-run `deploy/vercel-env.sh .env.local`, then `vercel deploy --prod`. On the website, **Logs** shows what happened. |
| `Refusing to start ... PC_BASE_URL must be https://` | `PC_BASE_URL` still says `http://localhost...`. |
| The build log says *Failed to open file README.md* | You're on an old copy. Run `git pull`. |

**Next:** [6. Connect ChatGPT, Claude, Gemini and Grok](06-connect-your-ai-apps.md)
