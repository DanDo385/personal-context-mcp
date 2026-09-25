#!/usr/bin/env bash
# One command from "settings filled in" to "live and verified":
#   preflight -> migrate -> check -> push settings to Vercel -> deploy -> health check
#
#   deploy/go-live.sh            uses deploy/admin.env.op + deploy/production.env.op (1Password)
#                                if they exist, otherwise .env for everything
#   deploy/go-live.sh .env       use one plain settings file for everything
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f deploy/local.env ]] && source deploy/local.env

PLAIN="${1:-}"
if [[ -z "$PLAIN" && -f deploy/admin.env.op && -f deploy/production.env.op ]]; then
  run() { op run --env-file=deploy/admin.env.op -- "$@"; }
  PRODUCTION=deploy/production.env.op
  URL="$(op run --no-masking --env-file=deploy/production.env.op -- printenv PC_BASE_URL)"  # not a secret
  deploy/preflight.sh
else
  PLAIN="${PLAIN:-.env}"
  [[ -f "$PLAIN" ]] || { echo "No settings at $PLAIN. Run: uv run personal-context-mcp init" >&2; exit 1; }
  run() { "$@"; }
  PRODUCTION="$PLAIN"
  URL="$(grep -E '^PC_BASE_URL=' "$PLAIN" | head -1 | cut -d= -f2- | tr -d '"'"'")"
fi
[[ "$URL" == https://* ]] || { echo "PC_BASE_URL must be an https:// address (got: ${URL:-nothing})." >&2; exit 1; }

echo "==> Creating/updating the database tables"
run uv run personal-context-mcp migrate
echo "==> Checking settings, database, storage and classifier"
run uv run personal-context-mcp check
echo "==> Copying settings to Vercel"
deploy/vercel-env.sh "$PRODUCTION"
echo "==> Deploying"
if [[ -z "${VERCEL_TOKEN:-}" && -n "${VERCEL_TOKEN_REF:-}" ]]; then VERCEL_TOKEN="$(op read "$VERCEL_TOKEN_REF")"; fi
vercel deploy --prod ${VERCEL_TOKEN:+--token "$VERCEL_TOKEN"} >/dev/null
echo "==> Verifying $URL"
for attempt in 1 2 3 4 5 6; do
  body="$(curl -s --max-time 20 "$URL/healthz" || true)"
  [[ "$body" == "ok" ]] && break
  sleep 5
done
if [[ "$body" != "ok" ]]; then
  echo "Deployed, but $URL/healthz answered: ${body:-nothing}" >&2
  echo "See the deployment logs on vercel.com, and docs/troubleshooting.md." >&2
  exit 1
fi
unauth="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -X POST "$URL/mcp" -H 'content-type: application/json' -d '{}')"
[[ "$unauth" == "401" ]] || { echo "Warning: an unauthenticated request got $unauth, expected 401." >&2; exit 1; }
echo
echo "Live: $URL"
echo "Add this address as a custom connector in each AI app: $URL/mcp"
echo "Google sign-in redirect URI must include: $URL/auth/callback"
