# Deploying personal-context-mcp

Production runs on **Vercel** at **`https://context.magro.dev`**, with its own
**Supabase** project, and authenticates with **Google OAuth using a client that
belongs to this project alone**. Every secret lives in one 1Password item,
`Dev / Personal Context MCP`. The files in this directory contain only
`op://` references and are resolved at run time by `op run`.

| File | Used by |
| --- | --- |
| `production.env.op` | `vercel-env.sh`, which pushes it to Vercel's production environment |
| `admin.env.op` | commands run from the Mac against production: `migrate`, `check`, `import`, `reclassify`, `review` |
| `worker.env.op` | the Obsidian sync LaunchAgent |
| `preflight.sh` | refuses to continue while any field in the 1Password item is still `REPLACE_ME` |
| `install-worker.sh`, `obsidian-sync.plist` | installs the Obsidian worker as a LaunchAgent |

## Why nothing is shared with phone-demo

`Dev / Supabase` (project `vwanrsxhwpoviasasvoa`) and
`Dev / Google OAuth Client Secret` (client `675437678581-…`) belong to other
work. `~/Code/phone-demo/.env.op` references the Google client, along with a
Calendar refresh token and a Workspace group calendar.

Reusing either would couple two unrelated systems:

- a leaked phone-demo secret would open this server
- rotating one would break the other
- a client configured as *Internal* to the magro.dev Workspace, or in
  *Testing* without djm385@gmail.com as a test user, would refuse
  djm385@gmail.com outright

Who may use this server is decided by `PC_ALLOWED_SUBJECTS=djm385@gmail.com`,
the account you choose in the Google sign-in popup. It does not depend on which
account owns the OAuth client, but creating the client under djm385@gmail.com
keeps ownership and access in the same place.

## One-time manual steps

### 1. Google Cloud project and OAuth client (signed in as djm385@gmail.com)

1. <https://console.cloud.google.com/projectcreate>. Name it `personal-context-mcp`.
   Billing is not needed.
2. **Google Auth Platform → Branding**:
   - App name: `Personal Context`
   - Support and developer email: djm385@gmail.com
3. **Audience**: choose **External**, then **Publish app**.
   - Only the non-sensitive `openid` and `email` scopes are requested, so
     publishing needs no Google verification.
   - Staying in *Testing* expires sign-ins after 7 days, which would force
     every connector to re-authenticate weekly.
4. **Data access**: add the scopes `openid`, `.../auth/userinfo.email`,
   `.../auth/userinfo.profile`.
5. **Clients → Create client → Web application**:
   - Name: `personal-context-mcp`
   - Authorised redirect URI: `https://context.magro.dev/auth/callback`
6. Copy these into 1Password `Personal Context MCP → Google OAuth`:
   - `google_client_id`
   - `google_client_secret`
   - `gcp_project`, the project ID

### 2. Supabase project

1. <https://supabase.com/dashboard/new>. Name it `personal-context-mcp`, pick
   the region nearest you and generate a database password. The free plan allows
   two active projects; pause an unused one if you hit the limit.
2. **Connect** (top bar), with the password substituted in:
   - **Transaction pooler** URI (port 6543) → `database_url_pooler`
   - **Session pooler** URI (port 5432) → `database_url_session`
3. **Project Settings → API Keys**:
   - the `service_role` (secret) key → `supabase_service_key`
   - the project URL → `supabase_url`
   - the ref → `supabase_project_ref`

`pgvector` is enabled by migration `0001`. The private storage buckets are
created by `personal-context-mcp check`.

### 3. DNS (Cloudflare, zone magro.dev)

Add `CNAME context → cname.vercel-dns.com` with **Proxy status: DNS only** (grey
cloud). Cloudflare's proxy would terminate TLS itself and buffer the MCP
streaming responses.

## Everything after that is scripted

```bash
deploy/preflight.sh                                                          # all fields filled?
op run --env-file=deploy/admin.env.op -- uv run personal-context-mcp migrate
op run --env-file=deploy/admin.env.op -- uv run personal-context-mcp check   # also creates buckets
deploy/vercel-env.sh                                                         # push env to Vercel
vercel deploy --prod --token "$(op read 'op://Dev/ej3urbcbpu6ks5lxjm2twbdd6e/credential')"
curl -s https://context.magro.dev/healthz
deploy/install-worker.sh                                                     # Obsidian worker
```

Then add `https://context.magro.dev/mcp` as a custom connector in each app
(`instructions/<provider>.md`), and choose **djm385@gmail.com** in the Google
popup.

## Why a subdomain and not magro.dev/personal-context-mcp

MCP clients discover OAuth from `/.well-known/oauth-protected-resource` and
`/.well-known/oauth-authorization-server` at the **host root**. On `magro.dev`
the portfolio site owns the root, so those requests would never reach this
server. A subdomain gives the server its own root.

## Why the endpoint is public and the repository private

The repository is private. The endpoint has to be reachable from the internet,
because ChatGPT, Claude, Gemini and Grok call it from their own clouds. It is
protected by:

- Google OAuth
- the one-account allowlist
- per-client scopes
- rate limiting

Vercel Deployment Protection must stay **off for the production domain**,
because a Vercel login wall would block the AI providers as well.

## Historical backfill runbook

Everything runs from this Mac against production through `deploy/admin.env.op`.
Alias it for convenience:

```bash
alias pcm='op run --env-file=deploy/admin.env.op -- uv run personal-context-mcp'
```

1. **Trial import.** Raw conversations are always kept. Pass 1 (Jev) classifies
   each one from a short excerpt after the local sensitivity screen.
   ```bash
   pcm import claude exports/claude.zip --limit 50
   pcm review                       # inspect what landed in the review queue
   ```
2. **Full import**, per provider: `pcm import chatgpt exports/chatgpt.zip`, and
   so on. Safe to re-run.
3. **Pass 2, deep extraction.** Claude (`PC_EXTRACTION_MODEL`, default
   `claude-opus-5-5`) reads the full transcripts Jev marked `deep_extraction`.
   Always look at the estimate first:
   ```bash
   pcm extract --dry-run --limit 500
   pcm extract --limit 50
   ```
4. **After changing questions or projects**, redo pass 1 from stored messages.
   No export archive is needed:
   ```bash
   pcm reclassify-conversations --dry-run
   pcm reclassify-conversations --limit 1000
   ```

5. **Build the wiki graph.** Python finds candidates, title mentions link
   directly, Jev judges the rest, Postgres stores the graph, and the worker
   renders it into the vault:
   ```bash
   pcm wiki seed-mocs --min-mentions 3     # maps from topics extraction found repeatedly
   pcm wiki add-moc "MCP" --description "Model Context Protocol"
   pcm wiki links --limit 500              # note links
   pcm wiki mocs --limit 500               # map membership and section
   pcm wiki render                         # queue changed notes and maps for the vault
   pcm wiki doctor                         # orphans, empty maps, pending reviews
   pcm review                              # links and memberships in the review band
   ```
   The thresholds are in `classifier/questions.py` (`WIKI_LINK_*`, `MOC_*`).
   Start conservative, review a sample, then tune.

Privacy knobs, all in `admin.env.op`:

- `PC_IMPORT_DEFAULT_SENSITIVITY`: the floor for every imported conversation.
- `PC_NO_EXTERNAL_SENSITIVITY`: which levels never leave the Mac, for both Jev
  and extraction. Add `private` to keep health, legal and finance conversations
  local as well.
