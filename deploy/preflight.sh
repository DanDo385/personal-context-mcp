#!/usr/bin/env bash
# Refuse to deploy while any 1Password field this project needs is still a
# placeholder. Prints field names only, never values.
set -euo pipefail
ITEM="Personal Context MCP"
missing=0
while IFS=$'\t' read -r section label value; do
  if [[ -z "$value" || "$value" == *REPLACE_ME* ]]; then
    echo "  missing: op://Dev/$ITEM/$section/$label"
    missing=1
  fi
done < <(op item get "$ITEM" --vault Dev --format=json | python3 -c '
import json, sys
for f in json.load(sys.stdin)["fields"]:
    s = (f.get("section") or {}).get("label")
    if s in ("Server", "Google OAuth", "Supabase"):
        print(s, f["label"], f.get("value") or "", sep="\t")
')
if [[ $missing -ne 0 ]]; then
  echo "Fill these in 1Password (see deploy/README.md), then re-run." >&2
  exit 1
fi
echo "1Password item complete."
