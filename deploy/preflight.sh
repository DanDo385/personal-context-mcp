#!/usr/bin/env bash
# 1Password users only: refuse to deploy while any field of your deployment's
# 1Password item is empty or still says REPLACE_ME. Prints field names, never
# values. Set PC_OP_ITEM (and PC_OP_VAULT) in deploy/local.env.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f deploy/local.env ]] && source deploy/local.env
VAULT="${PC_OP_VAULT:-Private}"
ITEM="${PC_OP_ITEM:-}"
if [[ -z "$ITEM" ]]; then
  echo "preflight: PC_OP_ITEM is not set in deploy/local.env; skipping the 1Password check."
  exit 0
fi
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
  echo "Fill these in the 1Password item, then re-run." >&2
  exit 1
fi
echo "1Password item complete."
