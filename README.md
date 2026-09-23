# personal-context-mcp

A provider-neutral shared memory, knowledge, list, project and artifact system,
exposed over MCP, that ChatGPT, Claude, Gemini and Grok all read and write.

It is not a chat UI. The native apps stay the interface; this sits underneath
them as shared state.

```mermaid
flowchart TB
    subgraph Providers["Native apps and subscriptions"]
        CG[ChatGPT]
        CL[Claude]
        GE[Gemini]
        GR[Grok]
    end

    CG & CL & GE & GR -->|MCP over HTTPS + OAuth 2.1| EDGE

    EDGE[["Public HTTPS edge<br/>Cloud Run"]] --> MCP

    subgraph Service["personal-context-mcp"]
        MCP[MCP tool surface<br/>48 tools]
        RULES[Deterministic rules]
        POLICY[Confidence policy engine]
        MCP --> RULES --> JEV[TypeSafe Jev<br/>System One classifier]
        JEV --> POLICY
        RULES -. Jev unavailable .-> POLICY
    end

    MCP --> PG[(Supabase Postgres<br/>+ pgvector)]
    MCP --> OS[(Supabase Storage<br/>artifact bytes)]
    PG --> JOBS[[sync_jobs queue]]

    JOBS -.->|outbound poll only| WORKER
    WORKER[obsidian-sync<br/>on your Mac] --> VAULT[(Obsidian vault)]

    style EDGE fill:#1f2937,color:#fff
    style Service fill:#0f172a,color:#fff
    style WORKER fill:#065f46,color:#fff
```

The dotted arrow is the important one: **the cloud service never reaches into
your machine.** It enqueues jobs; the laptop polls outbound and writes.

---

## Contents

- [What it actually does](#what-it-actually-does)
- [What MCP cannot do](#what-mcp-cannot-do)
- [Architecture](#architecture)
- [Memory layers](#memory-layers)
- [Context retrieval](#context-retrieval)
- [Session checkpoints](#session-checkpoints)
- [Lists](#lists)
- [Artifacts](#artifacts)
- [Jev classification](#jev-classification)
- [Confidence policy](#confidence-policy)
- [Obsidian synchronisation](#obsidian-synchronisation)
- [Security model](#security-model)
- [Privacy](#privacy)
- [Bulk import](#bulk-import)
- [Setup](#setup)
- [Deployment](#deployment)
- [Provider setup](#provider-setup)
- [Backup and recovery](#backup-and-recovery)
- [Testing](#testing)
- [Verified](#verified)
- [Roadmap](#roadmap)

---

## What it actually does

| | |
| --- | --- |
| **Shared memory** | A decision made in Claude is retrievable in Gemini, with provenance saying it came from Claude and when. |
| **Session archive** | Conversations and messages, imported from provider exports or checkpointed live. |
| **Semantic retrieval** | Hybrid full-text + vector search across memories, summaries, notes and decisions. |
| **Projects** | The central graph node: conversations, memories, decisions, tasks, lists, notes and artifacts hang off it. |
| **Lists** | First-class rows, not Markdown. Add from ChatGPT, complete from Claude, tick a box in Obsidian. |
| **Artifacts** | Multi-file projects stay one artifact with their tree, hashes and manifest intact. |
| **Classification** | Jev decides what is important and where it belongs; Python decides what to do about it. |
| **Obsidian** | A curated, human-editable layer, synced by an outbound-only local worker. |

---

## What MCP cannot do

This deserves its own section because most write-ups of this idea quietly
assume otherwise.

**MCP does not give a server every chat turn.** There is no
`on_every_user_message`. The *model* decides to call a tool. So the system
distinguishes four things, and so should you:

```mermaid
flowchart LR
    A["A. Shared context<br/>retrieved through MCP<br/>when the model asks"] --> D
    B["B. Live durable state<br/>model explicitly calls<br/>checkpoint / list / artifact tools"] --> D
    C["C. Complete raw history<br/>imported from provider exports"] --> D
    E["D. Artifact bytes<br/>only when the client actually<br/>hands them to a tool"] --> D
    D[(Personal Context)]
```

**And it does not give the server your uploaded files.** If you attach an image
in the ChatGPT app, the model can see it; the MCP server usually cannot. So
artifacts carry an explicit status and the tools refuse to lie about it:

| `storage_status` | Meaning |
| --- | --- |
| `captured` | Bytes stored **and** SHA-256 verified. The only status that means "saved". |
| `referenced` | We know it existed. We do not have it. |
| `pending_import` | Expected to arrive via a provider export later. |
| `failed` | Capture was attempted and did not complete. |

The tool descriptions instruct every model never to say "saved" unless the
result reports `captured`, and `artifact_register_reference` explicitly refuses
to accept `status="captured"`.

---

## Architecture

### Sources of truth

```mermaid
flowchart TB
    subgraph PG["Postgres — structured state"]
        direction LR
        P1[conversations, messages]
        P2[summaries, memories]
        P3[projects, decisions, lists]
        P4[artifact metadata + manifests]
        P5[classification + audit log]
    end

    subgraph OS["Object storage — file bytes"]
        direction LR
        O1[images, video, audio]
        O2[documents, datasets]
        O3[code and web projects]
        O4[provider export archives]
    end

    subgraph OB["Obsidian — human layer"]
        direction LR
        B1[project notes, research]
        B2[list views]
        B3[artifact READMEs]
    end

    PG -->|"renders"| OB
    OS -->|"links, and mirrors if small"| OB
```

Three rules that follow from this, and that the code enforces:

- Obsidian is **not** the raw transcript store.
- Postgres `bytea` is **not** the artifact store.
- The vault is a *rendering* of canonical state, plus a place humans edit. When
  both sides change, the worker reports a conflict rather than picking a winner.

### Repository layout

```
personal-context-mcp/
├── migrations/               # numbered SQL, applied once, hash-checked
├── instructions/             # per-provider setup + the shared instruction
├── skills/                   # 8 portable workflow skills
├── scripts/                  # per-provider import wrappers
├── src/personal_context_mcp/
│   ├── config.py             # settings + startup refusals
│   ├── server.py             # Starlette app, routes, lifespan
│   ├── services.py           # explicit service wiring
│   ├── models.py             # the shapes that cross the MCP boundary
│   ├── auth/                 # providers, scopes, principal, rate limit, KV
│   ├── mcp/                  # tool / prompt / resource registration
│   ├── db/                   # asyncpg pool + migration runner
│   ├── memory/ sessions/ projects/ lists/ notes/
│   ├── retrieval/            # hybrid search + context packages
│   ├── artifacts/            # keys, manifests, archive guards, service
│   ├── classifier/           # base, questions, rules, jev, policy, service
│   ├── storage/              # object store: supabase | local
│   ├── embeddings/           # provider abstraction + privacy gate
│   ├── obsidian/             # path validation, rendering, job queue
│   ├── importers/            # per-provider adapters + pipeline
│   ├── review/ audit/
│   └── worker_api.py         # the local worker's outbound surface
├── src/obsidian_sync/        # the local worker
└── tests/
```

---

## Memory layers

Retrieval prefers the **smallest sufficient layer**. Raw transcripts are a last
resort, never a default.

```mermaid
flowchart TB
    L1["Layer 1 — Raw archive<br/><i>complete imported conversations</i>"]
    L2["Layer 2 — Session summaries<br/><i>one compact record per conversation</i>"]
    L3["Layer 3 — Durable memory<br/><i>stable facts, preferences, decisions</i>"]
    L4["Layer 4 — Active project state<br/><i>goals, open tasks, live decisions</i>"]
    L5["Layer 5 — Obsidian knowledge<br/><i>human-readable, human-editable</i>"]

    L1 --> L2 --> L3 --> L5
    L3 --> L4

    R{{"context_bootstrap"}}
    R -.->|first| L4
    R -.->|then| L3
    R -.->|then| L5
    R -.->|then| L2
    R -.->|only if nothing else answered| L1
```

---

## Context retrieval

Hybrid: Postgres full-text search unioned with pgvector cosine similarity,
reranked by importance, recency and project association, then packed to a token
budget.

```
context_bootstrap(user_message, token_budget=2500, project=None)
context_search(query, limit, project, provider, include_messages=False)
context_for_topic(topic, token_budget, project)
context_recent(limit, provider, project)
```

`context_bootstrap` is the one that matters. It always includes current project
state and the most important durable memories, whether or not they matched the
query — "what am I working on" is context the answer usually needs even when
those words do not appear in the question.

Every returned item carries provenance:

| Kind | Trust |
| --- | --- |
| `user_statement` | The user said it. Fact. |
| `assistant_generated` | A model concluded it. Hypothesis. |
| `external_source` | Research. Cite it. |
| `tool_result` | Output of a tool. |
| `imported_metadata` | Derived mechanically from an import. Weakest. |

**Embeddings are optional.** With `PC_EMBEDDING_PROVIDER=none` retrieval runs on
full-text search alone and nothing leaves the process. That is the default, so
the system is useful before you have picked an embedding vendor.

---

## Session checkpoints

One call, one transaction, idempotent.

```mermaid
sequenceDiagram
    participant M as Model
    participant S as personal-context-mcp
    participant J as Jev
    participant D as Postgres
    participant Q as sync_jobs

    M->>S: session_checkpoint(summary, decisions, facts, tasks, …)
    S->>J: one request, all questions, over compact state
    J-->>S: typed answers + probabilities + confidence
    Note over S: Policy engine maps answers to actions
    S->>D: BEGIN
    S->>D: conversation, summary, decisions, memories, tasks, lists, projects
    S->>D: COMMIT
    opt promote_to_vault = yes AND not sensitive
        S->>Q: enqueue vault write
    end
    opt confidence in the review band
        S->>D: review_queue row
    end
    S-->>M: created / updated / skipped, plus the routing that was applied
```

Idempotency comes from deterministic keys, not from the caller remembering
what it already sent:

- conversations: `(provider, provider_conversation_id)` — and when a provider
  gives no id, a stable synthetic one from provider + title + day
- decisions, project events: content hash
- memories: content hash, then subject similarity, then embedding distance
- list items: normalised text hash

Calling `session_checkpoint` twice with the same content produces one of
everything. The result reports what was skipped and why.

---

## Lists

Lists are rows, not Markdown files.

```
list_search()   list_get()      list_create()
list_add_item() list_update_item() list_complete_item()
list_move_item() list_archive_item()
list_recent()   list_find_item()
```

Postgres is canonical. Obsidian renders a view, with each item carrying its
UUID so a checkbox ticked by hand flows back:

```markdown
- [ ] Configure the Gemini connector <!-- pc:id=8f14e45f-ceea-467a-9ba3-6b5f2c3d1e7a -->
- [x] Ship the MCP server            <!-- pc:id=c9f0f895-fb98-4b6a-a2f5-2a5d2c9e1b44 -->
```

Identity comes **only** from that marker. An edited line without one is
reported as unmatched, never matched by text — ticking the wrong item because
two lines read similarly would be worse than doing nothing.

---

## Artifacts

A four-file web project is **one** artifact with four files, not four
artifacts.

```
projects/attention-is-all-you-need/artifacts/interactive-explainer-v1/
    manifest.json
    index.html
    styles.css
    app.js
    assets/attention-head.png
```

Unassigned content goes to a library prefix instead:

```
library/{images,video,audio,documents,data,code,research-assets,reference,inbox}/YYYY/MM/<slug>-v<n>/
```

**Object keys are always generated by `artifacts/keys.py`** from structured
inputs. No model, and no classifier, ever supplies a path. Supplied filenames
are sanitised segment by segment and the original is kept in metadata.

Every compound artifact gets a manifest, generated in Python from the stored
rows — so it only ever lists files whose hashes we computed from bytes we held:

```json
{
  "manifest_version": 1,
  "artifact_id": "…",
  "title": "Transformer Explainer",
  "project": "attention-is-all-you-need",
  "type": "web_project",
  "version": 1,
  "storage_status": "captured",
  "file_count": 3,
  "files": [
    {"path": "index.html", "sha256": "…", "mime": "text/html", "bytes": 4821},
    {"path": "styles.css", "sha256": "…", "mime": "text/css",  "bytes": 1203},
    {"path": "app.js",     "sha256": "…", "mime": "text/javascript", "bytes": 9044}
  ]
}
```

Revisions bump the version and link to the predecessor via a `revision_of`
relationship, rather than leaving two unrelated copies.

### Archive safety

Provider export ZIPs come from outside the trust boundary. Every extraction
enforces, in order: zip-slip rejection (no `..`, absolute paths, drive letters
or symlinks), an entry-count cap, a per-entry size cap, a total size cap, and a
compression-ratio cap that refuses a bomb *before* expanding it. Members are
streamed in bounded chunks and checked against the bytes actually read, because
the declared size in a ZIP header is attacker-controlled.

---

## Jev classification

[Jev](https://docs.typesafe.ai) is a System One model: it answers typed
questions about state and returns calibrated probabilities. It never writes
prose, never returns a path, and never decides what to do.

```mermaid
flowchart LR
    IN[Object to classify] --> R{Deterministic rules}
    R -->|"MIME, extension, size,<br/>hash, file tree, duplicates"| FACTS[Established facts]
    FACTS --> JEV{{Jev — one request,<br/>all questions in parallel}}
    JEV -->|"typed answers +<br/>probabilities + confidence"| POL{Policy engine}
    POL -->|">= threshold"| AUTO[Act]
    POL -->|"middle band"| REV[Review queue]
    POL -->|"below"| DEF[Leave unassigned]
    R -.->|Jev unavailable| FALL[Conservative fallback<br/>+ reclassification queue]
```

**Rules before AI.** Anything ordinary code can answer is answered by ordinary
code: MIME type from extension, artifact type from the file tree, duplicate
detection from SHA-256. Jev is asked only what needs semantic judgement.

Every question and every threshold lives in one reviewable file,
`classifier/questions.py`, because those are the parts a human actually needs
to check. Independent questions about the same state go in **one** request —
Jev ingests the state once and evaluates them in parallel.

| Primitive | Used for |
| --- | --- |
| `Noul` | one independent yes/no, e.g. "does this create a concrete unfinished action?" |
| `Choice` | one option from a fixed set, always including a no-match option |
| `Score` | an ordered rubric whose levels describe concrete situations |

### Every decision is logged

`classification_events` records the classifier, model id, TypeSafe request id,
questions version, input hash, full answers with probabilities, confidence,
action taken, whether it was degraded, and whether a human later overrode it.
That table is what makes it possible to ask, months later, whether the
classifications were any good — and `review_reclassify` writes the override
back onto the originating event.

### When Jev is down

The database, memory, retrieval and every MCP tool keep working. Classification
falls back to deterministic rules that are deliberately *unconfident*: they
will save what you explicitly supplied, but they will not assign a project on a
guess and they will not write to your vault. The object is queued for
reclassification and the checkpoint result tells the model to say so.

---

## Confidence policy

The answer says *what*; confidence says *whether to act*. Thresholds scale with
the cost of being wrong.

| Band | Default | Behaviour |
| --- | --- | --- |
| Auto | ≥ 0.85 | Act, when the action is reversible |
| Review | 0.55 – 0.85 | Act but flag, or route to the review queue |
| Defer | < 0.55 | Leave unassigned; do not guess |

Multiplied per action class, because writing into someone's filesystem is not
the same as adding a tag:

| Action class | Multiplier | Example |
| --- | --- | --- |
| `reversible` | ×1.00 | tag an artifact, associate a project |
| `visible` | ×1.06 | write a note into the vault |
| `superseding` | ×1.12 | supersede or retract existing content |

Nouls carry no confidence value — the probability *is* the signal — so they use
a separate band: ≥ 0.80 yes, ≤ 0.20 no, and anything between is "unsure",
never "slightly yes".

Historical bulk imports use a stricter threshold (0.93) and do not promote to
the vault at all, so importing years of history does not flood it.

All of these are configuration values, not constants. They are engineering
defaults to be tuned against your own results.

---

## Obsidian synchronisation

```mermaid
sequenceDiagram
    participant S as personal-context-mcp (cloud)
    participant D as sync_jobs
    participant W as obsidian-sync (your Mac)
    participant V as Vault

    S->>D: enqueue (validated path, rendered content, expected sha)
    loop every 20s, outbound only
        W->>S: POST /worker/jobs/lease   (x-worker-key)
        S-->>W: leased jobs + rendered Markdown
        W->>W: re-validate the path independently
        W->>V: read current file, compare hashes
        alt file matches what we expected
            W->>V: atomic write (temp file + rename)
            W->>S: ack done + new sha
        else file changed on disk
            W->>S: ack conflict — refuses to overwrite a human edit
        end
    end
    opt human edited the vault
        W->>S: POST /worker/edits
        S->>S: new canonical version, provenance = user_statement
    end
```

Properties that fall out of this:

- **No inbound access to your Mac.** No port forwarding, no public SSH, no
  Obsidian REST API, no exposed filesystem. The laptop dials out.
- **The server keeps working with the laptop off.** Only vault sync pauses.
- **Human edits are never silently overwritten.** A mismatch is a conflict.
- **Paths are validated twice.** The server builds and validates; the worker
  re-validates on arrival. The cloud service is the component most likely to be
  compromised and the worker is the one with filesystem access, so the worker
  does not trust it.

Rejected by both layers: `..` traversal, absolute paths, drive letters, UNC
paths, NUL and control characters, reserved device names, dotfiles and
dot-directories (so nothing can write `.obsidian/` or `.git/`), symlinks
anywhere along the path, unknown roots, and unknown file extensions.

`sync_status` reports the queue and the worker's last activity, so a model can
answer "did that reach my vault" honestly rather than assuming.

---

## Security model

This contains your entire private conversation history. The URL being public
does not make the data public.

```mermaid
flowchart LR
    P[Provider cloud] -->|HTTPS| E[Public edge]
    E --> O{OAuth 2.1 + PKCE<br/>audience-validated}
    O -->|token valid AND<br/>subject on the allowlist| A[Application layer]
    O -->|anything else| X[401 / 403<br/>zero information]
    A --> DB[(Postgres)]
    A --> ST[(Private buckets)]
```

**A hidden URL is not authentication.** `PC_SERVER_ID` adds a high-entropy path
segment as defence in depth, and the test suite asserts that knowing it gets
you exactly as far as not knowing it.

### What an unauthenticated request learns

Nothing. `/` returns the service name, the endpoint and the fact that
authorization is required. No conversation titles, filenames, project names,
memory snippets, database ids, note paths, artifact metadata or user identity.
This is enforced by tests, not just intended.

### Standards

- OAuth 2.1 with PKCE, per the MCP 2026-07-28 authorization spec
- RFC 9728 Protected Resource Metadata at `/.well-known/oauth-protected-resource`
- RFC 8707 resource indicators — tokens are validated for **this** server's
  audience and no other
- RFC 7591 Dynamic Client Registration, presented to MCP clients by the proxy.
  **Grok requires this** — its connector form does not accept a static client
  id — which makes it the binding constraint across all four providers.
- RFC 6750 `insufficient_scope` challenges naming exactly the missing scopes

### Two-layer authorization

Worth stating plainly, because the honest version is less tidy than the
marketing version:

1. **Identity** — OAuth against Google (or GitHub, or an external IdP). Proves
   the caller acts for an account on `PC_ALLOWED_SUBJECTS`. A valid token
   belonging to somebody else is rejected exactly like a forged one.
2. **Capability** — the `context:read` / `memory:write` / … scopes. Google
   cannot mint scopes it has never heard of, so in proxy mode these are granted
   **server-side per registered client** from `PC_CLIENT_SCOPE_POLICY`, rather
   than requested by the client. It is a real, enforced boundary — FastMCP
   turns an unmet scope into a spec-correct 403 — but it is administered by you
   rather than negotiated in the OAuth flow.

   In `PC_AUTH_MODE=jwt`, where the IdP *does* mint these scopes, the token is
   authoritative instead and nothing else changes. The abstraction is there so
   that upgrade is a config change.

A client that matches no policy entry gets **read-only** by default.

### Scopes

```
context:read    memory:read     memory:write
sessions:read   sessions:write  projects:read   projects:write
lists:read      lists:write     notes:read      notes:write
artifacts:read  artifacts:write review:write
```

### Deliberately absent

No `execute_sql`, no `shell`, no `arbitrary_http_request`, no
`write_file(any_path)`, no generic delete. Tools are narrow and typed. The
closest thing to a delete is `list_archive_item`, which keeps the row.

### Also implemented

Per-principal token-bucket rate limiting · full audit log of every
AI-originated mutation · secure-by-default security headers and CSP ·
CORS closed to browser origins · no directory listing · private buckets,
verified at startup · short-lived signed URLs, shorter for sensitive
artifacts · secret redaction in logs · startup refusal of unsafe
configurations (`auth_mode=none` or `static` in production, plain HTTP in
production, OAuth without an allowlist).

### Startup refusals

The server will not boot with an unsafe configuration. `personal-context-mcp check`
reports every problem at once rather than one per run.

---

## Privacy

Three sensitivity levels, set per memory, conversation, note and artifact:
`normal`, `private`, `highly_sensitive`.

**Privacy routing happens before any external call, not after.** Content at a
level listed in `PC_NO_EXTERNAL_SENSITIVITY` is never sent to the embedding
API and never sent to Jev — the check is the first thing `JevClassifier._ask`
does, and it falls back to local rules instead. Sensitive content also never
auto-promotes to the vault, whatever the classifier said, and gets a shorter
signed-URL lifetime.

Text that *is* sent externally is redacted first: email addresses, API-key
shapes, bearer tokens, long digit runs and PEM private keys.

---

## Bulk import

```
personal-context-mcp import chatgpt ~/Downloads/chatgpt-export.zip
personal-context-mcp import claude  ~/Downloads/claude-export.zip
personal-context-mcp import gemini  ~/Downloads/takeout.zip
personal-context-mcp import grok    ~/Downloads/grok-export.zip
personal-context-mcp import auto    ~/Downloads/unknown-export.zip
```

Pipeline: preserve the original archive in cold storage (hashed) → detect and
parse → insert conversations and messages → ingest attachments, deduplicated by
SHA-256 across the whole corpus → derive a summary per conversation → classify
with historical thresholds → create memory candidates only where confidence is
high → associate projects → queue ambiguous cases for review → emit a report.

**Re-running is a no-op.** Conversations key on provider ids, attachments on
content hash, and the archive's own hash is recorded.

**Summaries from imports are mechanical, not model-written.** A bulk import of
years of history should not cost thousands of LLM calls, and a truthful excerpt
beats a hallucinated précis. They are marked `imported_metadata`, never
`assistant_generated`, because no assistant wrote them.

### Known export-format limitations

Reported by the importer, not hidden:

| Provider | Limitation |
| --- | --- |
| ChatGPT | Uploaded files and generated images are captured when present as archive members. Files rendered only in the UI may be named without a corresponding member, and become `pending_import`. |
| Claude | Attachment metadata and extracted text are reliable; original binaries are not always included. Extracted text is captured as a text artifact — that content really is in the export. |
| Gemini | Takeout is an **activity log** with no stable conversation id. Records are grouped into one synthetic conversation per day, so multi-day threads appear split and two same-day chats may appear merged. Takeout does not include files generated in Gemini at all, and gives no filename to reference them by — so they cannot even be recorded as references. |
| Grok | Format is less settled. The adapter probes for the shapes it knows and reports what it could not read rather than skipping silently. Generated images are not reliably included. |

---

## Setup

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Postgres 13+ with `pgvector` (Supabase provides it). `pgcrypto` is not
  required - `gen_random_uuid()` is core - and `pg_trgm` is optional: it
  only accelerates name lookups and every query works without it.
- A Google Cloud OAuth client, for production auth
- Optionally, a [TypeSafe API key](https://console.typesafe.ai/keys) for Jev

### Local

```bash
git clone <this repo> && cd personal-context-mcp
uv sync --all-groups
cp .env.example .env         # then edit it

# Postgres with pgvector, the quick way
docker compose up -d db

uv run personal-context-mcp migrate
uv run personal-context-mcp check
uv run personal-context-mcp serve
```

`check` validates configuration, connectivity, migrations, bucket privacy and
Jev reachability, and prints your MCP endpoint.

### Migrations

```bash
uv run personal-context-mcp migrate            # apply
uv run personal-context-mcp migrate --status   # what is pending
```

Migrations are numbered SQL files applied once each inside a transaction and
recorded with their hash. Editing an already-applied migration is refused —
add a new one.

---

## Deployment

Cloud Run + Supabase. The server must keep working with every machine you own
switched off; only vault sync pauses.

```bash
PROJECT=your-gcp-project
REGION=us-central1

gcloud run deploy personal-context-mcp \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 4 \
  --cpu 1 --memory 1Gi \
  --timeout 300 \
  --set-env-vars "PC_ENVIRONMENT=prod,PC_BASE_URL=https://ctx.example.com,PC_AUTH_MODE=google,PC_STORAGE_BACKEND=supabase,PC_CLASSIFIER=jev" \
  --set-secrets "PC_DATABASE_URL=pc-database-url:latest,\
PC_SUPABASE_SERVICE_KEY=pc-supabase-key:latest,\
PC_OAUTH_CLIENT_ID=pc-oauth-id:latest,\
PC_OAUTH_CLIENT_SECRET=pc-oauth-secret:latest,\
PC_OAUTH_SIGNING_KEY=pc-signing-key:latest,\
PC_TYPESAFE_API_KEY=pc-typesafe-key:latest,\
PC_WORKER_API_KEY=pc-worker-key:latest"
```

`--allow-unauthenticated` refers to *Cloud Run's* IAM layer, which must be open
so provider clouds can reach the endpoint. The application's own OAuth is what
protects the data.

Then apply migrations once against the production database:

```bash
PC_DATABASE_URL="<prod url>" uv run personal-context-mcp migrate
```

### Why not a provider tunnel

Some providers offer private tunnels. Using one would tie the architecture to
a single vendor, and Claude, Gemini and Grok all need ordinary HTTPS
reachability. Public HTTPS with real OAuth is the only shape that serves all
four.

---

## Provider setup

Full walkthroughs, including each provider's limitations:

- [ChatGPT](instructions/chatgpt.md)
- [Claude](instructions/claude.md)
- [Gemini](instructions/gemini.md) — **does not require Spark**
- [Grok](instructions/grok.md) — **requires DCR**
- [The shared custom instruction](instructions/shared.md)

### Obsidian worker

Runs on your Mac. Outbound only.

Note the `OBSIDIAN_SYNC_` prefix rather than a bare `OBSIDIAN_`: some
machines already export `OBSIDIAN_VAULT_PATH` for unrelated tools, and an
environment variable beats a `.env` file - which would silently redirect
where this worker writes.

```bash
uv sync
cat > .env <<'EOF'
OBSIDIAN_SYNC_API_URL=https://ctx.example.com
OBSIDIAN_SYNC_API_KEY=<same value as PC_WORKER_API_KEY>
OBSIDIAN_SYNC_VAULT_PATH=~/Obsidian/Vault
EOF

uv run obsidian-sync check          # verify vault + credentials
uv run obsidian-sync run --dry-run  # see what it would write
uv run obsidian-sync run
```

To keep it running, a LaunchAgent calling `obsidian-sync run` is enough. It
tolerates being offline: the queue is server-side, so nothing is lost.

---

## Backup and recovery

| Layer | Mechanism |
| --- | --- |
| Postgres | Supabase automated daily backups + PITR on paid plans. Take your own `pg_dump` before any migration. |
| Object storage | Supabase Storage replication. Original provider exports live in a separate restricted bucket, so history is recoverable even if the database is lost. |
| Obsidian | Your existing vault backup (iCloud, Git, Time Machine). The vault is a rendering, so it is the least critical layer. |
| Secrets | Secret Manager, versioned. Rotate by adding a version and redeploying. |

**Encryption.** TLS in transit everywhere. Supabase encrypts at rest.
Buckets are private and verified private at startup; all access is through
short-lived signed URLs. For especially sensitive artifacts, application-level
encryption before upload is the natural next step and is not yet implemented.

**Deletion.** Removing a memory retracts it and keeps provenance; nothing is
hard-deleted by an AI-facing tool. Deleting an artifact's bytes is an operator
action against storage.

**Secret rotation.** Every secret is read from the environment. Rotating
`PC_OAUTH_SIGNING_KEY` invalidates issued tokens and forces clients to
re-authorize. Rotating `PC_WORKER_API_KEY` requires updating the worker's
`.env`.

---

## Testing

```bash
uv run pytest                      # unit tests, no external dependencies
uv run ruff check src tests
uv run mypy src

# Integration tests need a throwaway Postgres with pgvector.
# The fixture refuses a database whose name does not contain "test".
export PC_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_context_mcp_test
uv run pytest
```

Coverage focuses where a regression would be expensive: path traversal, ZIP
guards, artifact capture honesty, memory deduplication, checkpoint
idempotency, confidence thresholds, classifier outage, privacy routing, scope
enforcement, and what an unauthenticated caller can see.

---

## Verified

Everything below was executed against a real PostgreSQL 16 with pgvector, a
real HTTP server, and a real filesystem vault - not mocked.

| | |
| --- | --- |
| Unit + integration tests | **264 passed** |
| `ruff check` | clean |
| `mypy src` | clean, 77 files |
| Migrations | applied from scratch on a virgin database |
| MCP surface | 48 tools over Streamable HTTP, authenticated |

The full vertical slice, end to end over HTTP:

```
 [ 1] connected + authenticated; 48 tools advertised
 [ 2] memory_save -> created
 [ 3] memory_search -> 1 hit
 [ 4] project_create -> personal-ai
 [ 5] list_create -> personal-ai-tasks
 [ 6] list_add_item -> 'Test Gemini connector on iPhone'
 [ 7] session_checkpoint -> created={conversations:1, session_summaries:1,
                                     decisions:1, memories:2, tasks:1}
 [ 8] checkpoint is idempotent -> second call skipped={decisions:1, memories:1, tasks:1}
 [ 9] context_bootstrap -> items with provenance ['claude'], inside token budget
 [10] artifact_save_text -> capture_status=captured
 [11] artifact_save_bundle -> ONE artifact, 4 files, type=web_project
 [12] classification -> semantic_category=code_project, project=personal-ai
 [13] manifest -> tree preserved: index.html, public/diagram.svg, src/app.js, styles.css
 [14] hashes verified against the real bytes
 [15] reference artifact -> pending_import (not claimed as saved)
 [16] artifact_queue_for_vault -> 5 jobs
 [17] note_write -> 30-Research/mcp-oauth-across-providers.md queued
 [18] sync_status -> {approved: 6}
 [19] list_complete_item by stable id
 [20] final bootstrap across active project state
```

Then the local worker drained the queue and wrote real files:

```
20-Projects/personal-ai/Artifacts/personal-ai-dashboard/README.md
20-Projects/personal-ai/Artifacts/personal-ai-dashboard/index.html
20-Projects/personal-ai/Artifacts/personal-ai-dashboard/public/diagram.svg
20-Projects/personal-ai/Artifacts/personal-ai-dashboard/src/app.js
20-Projects/personal-ai/Artifacts/personal-ai-dashboard/styles.css
30-Research/mcp-oauth-across-providers.md
```

And the reverse direction:

```
 [1] a human edited the note in the vault
 [2] worker pushed it up -> changed=True version=2
 [3] canonical note now carries the human's text
 [4] the edit is retrievable through MCP
 [5] the human edits again before the next queued write lands
 [6] server write REFUSED -> conflict; will not overwrite a human edit
 [7] the file on disk is untouched
```

Bulk import, twice over the same archive:

```
first run:  conversations imported 1, messages 2,
            attachments captured 1, referenced_only 1, reviews queued 1
second run: imported 0, skipped_unchanged 1, messages 0
            "This exact archive has already been imported."
```

Jev was exercised against the live `api.typesafe.ai` endpoint. Without a key
the call returns `TypeSafeAuthenticationError`, the classifier degrades to
deterministic rules with a complete answer set, and every tool keeps working -
which is the behaviour that matters. Classification **quality** is unverified
until a real `PC_TYPESAFE_API_KEY` is configured.

### Not verified here

- The four providers' connector UIs. Setup is documented from their current
  docs; the OAuth flows need your own deployment and accounts to exercise.
- Supabase Storage. The slice ran on the local storage backend; the Supabase
  backend is implemented and bucket privacy is checked at startup, but it has
  not been run against a live project.
- Jev's classification accuracy, as above.
- Real provider export archives. The importers were tested against archives
  built to each documented format, not against your actual exports.

---

## Roadmap

Not built, deliberately:

1. **Browser extension** for capturing web conversation turns and artifact
   bytes at source — the only way to close the attachment gap for Gemini.
   Designed for, not built; the ingestion API it would target is the same one
   the worker uses.
2. **Application-level encryption** for `highly_sensitive` artifacts.
3. **Embedding backfill worker** for content saved while embeddings were off.
4. **Classification quality review** over `classification_events` — the data is
   already being collected for exactly this.
5. **Provider API ingestion** where official conversation APIs exist, to reduce
   reliance on manual exports.
6. **Multi-user support.** Single-user assumptions are isolated to the auth
   layer and the allowlist; the schema already carries the provenance needed.
