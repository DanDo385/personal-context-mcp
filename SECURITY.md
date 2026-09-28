# Security

personal-context-mcp holds people's private conversation history, so security reports are taken
seriously and handled first.

## Reporting a vulnerability

**Please don't open a public issue.** Use GitHub's private reporting instead: on the repository page,
go to **Security → Report a vulnerability**. Include what you found, how to reproduce it, and what an
attacker could do with it. You'll get a reply within a few days.

## Scope

In scope: anything that lets someone read or change another person's data, get past the account
allowlist or scopes, write outside the Obsidian vault, send content marked sensitive to an outside
service, or learn anything from an unauthenticated request.

Out of scope: problems that need an attacker to already have your `.env.local`, your 1Password account, your
Supabase service key or your Vercel account.

## For people running it

- Keep `PC_ALLOWED_SUBJECTS` to your own account(s).
- Never share your `.env.local`, Supabase service role key, Google client secret or `PC_WORKER_API_KEY`.
- Run `uv run personal-context-mcp check` after changing settings; the server refuses to start with an
  unsafe production configuration.
- Update regularly: `git pull && uv sync && uv run personal-context-mcp migrate && vercel deploy --prod`.

The design is described in [Architecture: Security model](docs/architecture.md#security-model).
