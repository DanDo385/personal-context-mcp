#!/usr/bin/env bash
# Install the Obsidian sync worker as a LaunchAgent. It runs through zsh so
# ~/.zshenv supplies OP_SERVICE_ACCOUNT_TOKEN, and through `op run` so the
# worker key is read from 1Password at start and never written to disk.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.openclaw.personal-context-obsidian-sync
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Personal Context"
LOGS="$HOME/Library/Logs/personal-context-mcp"

mkdir -p "$VAULT" "$LOGS"
(cd "$REPO" && uv sync --extra worker --quiet)

sed -e "s#__REPO__#$REPO#g" -e "s#__LOGS__#$LOGS#g" -e "s#__LABEL__#$LABEL#g" \
  "$REPO/deploy/obsidian-sync.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL. Vault: $VAULT"
echo "Logs: tail -f $LOGS/obsidian-sync.err.log"
