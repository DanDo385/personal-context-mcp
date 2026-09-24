#!/usr/bin/env bash
# Copy your production settings into Vercel's "Production" environment.
#
#   deploy/vercel-env.sh [SETTINGS_FILE]
#
# SETTINGS_FILE is either
#   - a plain file of NAME=value lines (for example one written by
#     `personal-context-mcp init --output .env.production`), or
#   - a file of op:// references (deploy/production.env.op), resolved by the
#     1Password CLI at run time so no secret is ever written to disk.
# Default: deploy/production.env.op if it exists, otherwise .env.production.
#
# Each value is handed to the Vercel CLI on stdin, so none shows up in your
# shell history or in the process list.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f deploy/local.env ]] && source deploy/local.env

FILE="${1:-}"
if [[ -z "$FILE" ]]; then
  if [[ -f deploy/production.env.op ]]; then FILE=deploy/production.env.op; else FILE=.env.production; fi
fi
[[ -f "$FILE" ]] || { echo "No settings file at $FILE. See docs/guides/05-put-it-online.md." >&2; exit 1; }
PROJECT="${PC_VERCEL_PROJECT:-personal-context-mcp}"

if [[ -z "${VERCEL_TOKEN:-}" && -n "${VERCEL_TOKEN_REF:-}" ]]; then
  VERCEL_TOKEN="$(op read "$VERCEL_TOKEN_REF")"
fi
[[ -n "${VERCEL_TOKEN:-}" ]] && export VERCEL_TOKEN

PUSH=$(cat <<'PY'
import os, re, subprocess, sys
path, project, resolved = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
values = {}
for line in open(path, encoding="utf-8"):
    m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
    if not m:
        continue
    name, raw = m.groups()
    raw = re.sub(r"\s+#.*$", "", raw).strip().strip('"').strip("'")
    values[name] = os.environ[name] if resolved else raw
secret = re.compile(r"(KEY|SECRET|TOKEN|DATABASE_URL|CLIENT_ID)")
token = ["--token", os.environ["VERCEL_TOKEN"]] if os.environ.get("VERCEL_TOKEN") else []
for name, value in values.items():
    if name.startswith("OBSIDIAN_SYNC_"):
        continue  # the worker's settings stay on your computer
    kind = "--sensitive" if secret.search(name) else "--no-sensitive"
    subprocess.run(
        ["vercel", "env", "add", name, "production", "--force", kind,
         "--project", project, "--yes", "--non-interactive", *token],
        input=value.encode(), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    print(f"  set {name} ({kind.lstrip('-')})")
PY
)

if grep -q "op://" "$FILE"; then
  command -v op >/dev/null || { echo "$FILE uses op:// references but the 1Password CLI (op) is not installed." >&2; exit 1; }
  deploy/preflight.sh
  op run --env-file="$FILE" -- python3 -c "$PUSH" "$FILE" "$PROJECT" 1
else
  python3 -c "$PUSH" "$FILE" "$PROJECT" 0
fi
echo "Done. Deploy with: vercel deploy --prod"
