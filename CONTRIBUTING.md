# Working on personal-context

## Running a local Postgres without Docker

The test suite and the local slice need PostgreSQL with `pgvector`. If Docker
is available, `docker compose up -d db` is the simplest path.

Without Docker — for example on a macOS version Homebrew no longer builds
bottles for — `pgserver` ships a self-contained PostgreSQL **with pgvector
already built in**, as a pip wheel and no compiler:

```bash
uv pip install --target .tools/pgs pgserver

python - <<'PY'
import sys, pathlib
sys.path.insert(0, ".tools/pgs")
import pgserver
db = pgserver.get_server(str(pathlib.Path(".data/pg").absolute()), cleanup_mode=None)
print(db.get_uri())
db.psql("CREATE DATABASE personal_context_test;")
PY
```

It listens on a unix socket, so the DSN takes this shape:

```bash
export PC_TEST_DATABASE_URL="postgresql://postgres@/personal_context_test?host=/abs/path/.data/pg"
uv run pytest
```

`pgserver` bundles only `plpgsql` and `vector`. That is enough: the schema
requires `vector` and nothing else. `pgcrypto` is deliberately unused
(`gen_random_uuid()` is core since PostgreSQL 13) and `pg_trgm` is optional —
migration 0001 creates it if available and migration 0002 skips its indexes if
not, because it only accelerates the ILIKE name lookups.

## Checks

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
uv run pytest
```

Integration tests skip with a clear reason when `PC_TEST_DATABASE_URL` is
unset, and the fixture refuses a database whose name does not contain `test`
before it drops any schema.

## Migrations

Numbered SQL, applied once, recorded with a hash. **Editing an applied
migration is refused at runtime** — add a new file instead. This is deliberate:
a schema that silently diverges from its migration history is worse than a
failed startup.

## Where to look first

| Question | File |
| --- | --- |
| What does the classifier actually ask? | `classifier/questions.py` |
| When does it act on the answer? | `classifier/policy.py` |
| Where do object keys come from? | `artifacts/keys.py` |
| What paths may the vault worker write? | `obsidian/paths.py` |
| What can an unauthenticated caller see? | `tests/test_http_security.py` |
| How are scopes granted? | `auth/scopes.py`, `auth/provider.py` |

The first two are the ones a human should actually review. Everything the
model is asked, and every threshold that turns an answer into an action, is in
those two files on purpose.
