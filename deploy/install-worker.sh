#!/usr/bin/env bash
# Keep the Obsidian sync worker running in the background on a Mac, starting
# it again after a restart or a crash (a launchd "LaunchAgent").
#
# Settings come from, in order:
#   deploy/worker.env.op   op:// references, read from 1Password when it starts
#   .env.local             plain settings written by `personal-context-mcp init`
# Linux: see docs/guides/08-obsidian.md for the systemd equivalent.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[[ -f deploy/local.env ]] && source deploy/local.env

LABEL="${WORKER_LABEL:-com.personal-context-mcp.obsidian-sync}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/personal-context-mcp"
ENV_FILE="${WORKER_ENV_FILE:-}"
if [[ -z "$ENV_FILE" && -f deploy/worker.env.op ]]; then ENV_FILE=deploy/worker.env.op; fi

if [[ -n "$ENV_FILE" ]]; then
  command -v op >/dev/null || { echo "$ENV_FILE needs the 1Password CLI (op)." >&2; exit 1; }
  COMMAND="exec op run --env-file=$ENV_FILE -- .venv/bin/obsidian-sync run"
elif [[ -f .env.local || -f .env ]]; then
  COMMAND="exec .venv/bin/obsidian-sync run"
else
  echo "No settings found. Run: uv run personal-context-mcp init" >&2
  exit 1
fi

VAULT="${VAULT:-$HOME/Obsidian/Personal Context}"
mkdir -p "$VAULT" "$LOGS"
uv sync --extra worker --quiet

python3 - "$REPO/deploy/obsidian-sync.plist" "$PLIST" "$LABEL" "$REPO" "$LOGS" "$COMMAND" <<'PY'
import sys
from xml.sax.saxutils import escape
template, target, label, repo, logs, command = sys.argv[1:]
text = open(template).read()
for key, value in {"__LABEL__": label, "__REPO__": repo, "__LOGS__": logs, "__COMMAND__": command}.items():
    text = text.replace(key, escape(value))
open(target, "w").write(text)
PY
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL."
echo "Vault folder: $VAULT"
echo "Watch it work: tail -f $LOGS/obsidian-sync.err.log"
echo "Stop it:       launchctl bootout gui/$(id -u)/$LABEL"
