#!/usr/bin/env bash
# Update the "Personal Context (for Gemini)" Google Doc every week on a Mac
# (a launchd LaunchAgent). Runs `personal-context-mcp gemini-sync push`.
#
# Settings come from deploy/admin.env.op through `op run` if present,
# otherwise from .env.local. See docs/guides/11-gemini-without-custom-apps.md.
#   GEMINI_SYNC_WEEKDAY (0-6, Sunday=0; default 1 = Monday), GEMINI_SYNC_HOUR (default 9)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[[ -f deploy/local.env ]] && source deploy/local.env

LABEL="${GEMINI_SYNC_LABEL:-com.personal-context-mcp.gemini-sync}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/personal-context-mcp"

if [[ -f deploy/admin.env.op ]]; then
  command -v op >/dev/null || { echo "deploy/admin.env.op needs the 1Password CLI (op)." >&2; exit 1; }
  COMMAND="exec op run --env-file=deploy/admin.env.op -- .venv/bin/personal-context-mcp gemini-sync push"
else
  COMMAND="exec .venv/bin/personal-context-mcp gemini-sync push"
fi

mkdir -p "$LOGS"
uv sync --quiet
python3 - "$REPO/deploy/gemini-sync.plist" "$PLIST" "$LABEL" "$REPO" "$LOGS" "$COMMAND" \
  "${GEMINI_SYNC_WEEKDAY:-1}" "${GEMINI_SYNC_HOUR:-9}" <<'PY'
import sys
from xml.sax.saxutils import escape
template, target, label, repo, logs, command, weekday, hour = sys.argv[1:]
text = open(template).read()
for key, value in {"__LABEL__": label, "__REPO__": repo, "__LOGS__": logs, "__COMMAND__": command,
                   "__WEEKDAY__": weekday, "__HOUR__": hour}.items():
    text = text.replace(key, escape(value))
open(target, "w").write(text)
PY
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL (weekly)."
echo "Run it now:     launchctl kickstart gui/$(id -u)/$LABEL"
echo "Watch it:       tail -f $LOGS/gemini-sync.err.log $LOGS/gemini-sync.out.log"
echo "Remove it:      launchctl bootout gui/$(id -u)/$LABEL"
