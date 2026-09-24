#!/usr/bin/env bash
# Push deploy/production.env.op to the Vercel project's production
# environment. `op run` resolves the references into this process only; each
# value is handed to the Vercel CLI on stdin, so none appears in argv, shell
# history or a file.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=personal-context-mcp
deploy/preflight.sh

export VERCEL_TOKEN="${VERCEL_TOKEN:-$(op read 'op://Dev/ej3urbcbpu6ks5lxjm2twbdd6e/credential')}"
op run --env-file=deploy/production.env.op -- python3 - "$PROJECT" <<'PY'
import os, re, subprocess, sys
project = sys.argv[1]
names = [
    m.group(1)
    for line in open("deploy/production.env.op")
    if (m := re.match(r"^([A-Z][A-Z0-9_]*)=", line))
]
secret = re.compile(r"(KEY|SECRET|TOKEN|DATABASE_URL|CLIENT_ID)")
for name in names:
    kind = "--sensitive" if secret.search(name) else "--no-sensitive"
    subprocess.run(
        ["vercel", "env", "add", name, "production", "--force", kind,
         "--project", project, "--yes", "--non-interactive"],
        input=os.environ[name].encode(), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    print(f"  set {name} ({kind.lstrip('-')})")
PY
