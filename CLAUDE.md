# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --all-groups                       # install, including dev group
uv run personal-context migrate            # apply migrations (--status to see pending)
uv run personal-context check              # validate config, DB, migrations, buckets, Jev
uv run personal-context serve              # run the MCP server
docker compose up -d db                    # Postgres 17 + pgvector on :5432

uv run ruff check src tests
uv run ruff format src tests
uv run mypy src                            # packages personal_context, obsidian_sync
uv run pytest
uv run pytest tests/test_paths.py::test_name -x   # single test
```

`ruff check src tests`, `mypy src` and `pytest` are all expected to be clean before a change is
done.

### Tests

Two tiers, split by whether a database is needed:

- Unit tests (`test_paths`, `test_archives`, `test_classifier`, `test_auth`, `test_obsidian`,
  `test_importers`, `test_http_security`) need nothing external.
- `test_integration_*` modules carry `pytestmark = requires_db` and **skip** unless
  `PC_TEST_DATABASE_URL` is set. The `db` fixture drops and recreates the `public` schema on every
  test, and `conftest._test_dsn` aborts the whole run if the database name does not contain
  `test`.

```bash
export PC_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_context_test
uv run pytest
```

`CONTRIBUTING.md` documents a Docker-free Postgres (`pgserver`, pgvector included) for machines
without Homebrew bottles.

### Migrations

Numbered SQL in `migrations/`, applied once inside a transaction and recorded with a content hash.
**Editing an already-applied migration is refused at runtime** — add a new numbered file instead.
`VECTOR(1536)` in `0002` fixes `config.EMBEDDING_DIM`; changing the dimension requires a migration.

## Architecture

An MCP server (FastMCP mounted in Starlette) that is shared, provider-neutral memory for ChatGPT,
Claude, Gemini and Grok. Postgres+pgvector is canonical structured state, object storage holds
artifact bytes, Obsidian is a rendered human layer. `README.md` is the long-form design document;
the points below are the ones that span files.

**Request path.** `server.py` builds the FastMCP server (auth provider, rate-limit middleware,
tool registration via `mcp/register_all`) and mounts it in a Starlette app alongside `/worker/*`
and health routes. Its lifespan opens the asyncpg pool and calls `build_services`.

**Service wiring is explicit.** `services.py` constructs every service in dependency order into one
`Services` dataclass, stored in a module global and reached from tool functions with
`get_services()`. There is no DI framework; add new services to that function and dataclass.

**Tools are thin.** `mcp/tools_*.py` only declare the schema (`Annotated[... , Field(description=)]`),
enforce a scope with `auth=require_scopes(...)`, and delegate to a service in
`memory/ sessions/ projects/ lists/ notes/ artifacts/ retrieval/ review/`. Business logic and SQL
belong in the service, never in the tool module. Field descriptions are prompt surface — the model
reads them — so they are written as instructions to the caller.

**`models.py` is the MCP boundary.** Pydantic types with `extra="forbid"`; the `Literal` unions
there (`Provenance`, `Sensitivity`, `StorageStatus`, `ArtifactType`, …) are the vocabulary the rest
of the code and the SQL agree on.

**Classification is two-stage and must stay that way.** Deterministic rules
(`classifier/rules.py`) answer anything code can answer — MIME, extension, file tree, hashes,
duplicates. Jev (`classifier/jev.py`, TypeSafe) answers only typed semantic questions and returns
probabilities; it never returns prose, a path or an action. `classifier/policy.py` maps answers to
actions through confidence bands scaled by an action-class multiplier. Every question and threshold
lives in `classifier/questions.py` on purpose — those two files are what a human reviews. When Jev
is unavailable the result is marked `degraded`, the fallback deliberately refuses to guess a project
or write to the vault, and the object is queued for reclassification.

**`session_checkpoint` is the shared write path** (`sessions/service.py`, the largest module). One
transaction, and idempotent through deterministic keys — `(provider, provider_conversation_id)` for
conversations, content hashes for decisions/memories/list items — never through caller bookkeeping.
Preserve both properties when changing it.

**The cloud never reaches the Mac.** `obsidian/queue.py` only enqueues jobs (validated path,
rendered content, `expected_sha256`); `src/obsidian_sync/` polls `/worker/*` outbound with a shared
key, **re-validates the path itself**, compares hashes and acks `done` or `conflict`. The worker
does not trust the server — keep the second validation. Vault paths are rejected by both layers
(`obsidian/paths.py`): traversal, absolute paths, dotfiles, symlinks, unknown roots or extensions.

**Artifacts.** Object keys are generated only by `artifacts/keys.py` from structured inputs; no model
or classifier ever supplies a path. `storage_status` must stay honest — only `captured` means bytes
were stored and SHA-256 verified, and `artifact_register_reference` refuses `status="captured"`.
Manifests are generated in Python from stored rows. Provider ZIPs go through `artifacts/archives.py`
guards (zip-slip, entry count, per-entry and total size, compression ratio, streamed and checked
against bytes actually read).

**Privacy routing happens before any external call.** `JevClassifier._ask` and the embedding service
check sensitivity first; content at a level in `PC_NO_EXTERNAL_SENSITIVITY` never leaves the process,
never auto-promotes to the vault, and gets a shorter signed-URL TTL. Text that does go out is
redacted first.

**Auth is two layers.** Identity (OAuth 2.1 + PKCE against Google/GitHub, or `jwt` mode) plus an
allowlist of subjects; capability scopes (`auth/scopes.py`) granted server-side per client from
`PC_CLIENT_SCOPE_POLICY`, defaulting to read-only for an unmatched client. `PC_SERVER_ID` is defence
in depth only — `tests/test_http_security.py` asserts an unauthenticated caller learns nothing with
or without it. Keep that test honest when adding routes.

**Config is environment-only**, `PC_`-prefixed, in `config.py`, documented in `.env.example`.
`Settings.validate_startup()` reports *every* unsafe configuration at once and the server refuses to
boot on one (`auth_mode=none|static` in prod, plain HTTP in prod, OAuth without an allowlist).

**Embeddings are optional.** `PC_EMBEDDING_PROVIDER=none` is the default; retrieval falls back to
full-text search alone, so nothing may assume a vector is present.

### Consumers outside `src/`

- `skills/` — eight portable workflow skills handed to the providers.
- `instructions/` — per-provider connector setup plus the shared custom instruction.
- `scripts/` — thin wrappers over `personal-context import <provider>` / `reclassify`.
