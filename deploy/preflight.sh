#!/usr/bin/env bash
# Refuse to deploy while any 1Password field this project needs is still a
# placeholder. Prints field names only, never values.
set -euo pipefail
# The 1Password item holding this deployment's secrets, referenced by id so
# renaming it never breaks anything. Override for your own deployment.
VAULT="${PC_OP_VAULT:-Dev}"
ITEM="${PC_OP_ITEM:-2c74cf4sfqsj3cey2q6peuhwkq}"
missing=0
while IFS=$'\t' read -r section label value; do
  if [[ -z "$value" || "$value" == *REPLACE_ME* ]]; then
    echo "  missing: $section > $label   (op://$VAULT/$ITEM/$section/$label)"
    missing=1
  fi
done < <(op item get "$ITEM" --vault "$VAULT" --format=json | python3 -c '
import json, sys
for f in json.load(sys.stdin)["fields"]:
    s = (f.get("section") or {}).get("label")
    if s in ("Server", "Google OAuth", "Supabase"):
        print(s, f["label"], f.get("value") or "", sep="\t")
')
if [[ $missing -ne 0 ]]; then
  echo "Fill these in the 1Password item (see deploy/README.md), then re-run." >&2
  exit 1
fi
echo "1Password item complete."
