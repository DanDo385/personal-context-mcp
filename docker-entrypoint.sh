#!/bin/sh
# Migrate once, then run the image command. The default command is serve.
#
# Migrations run inside one transaction and are recorded with a content hash,
# so re-running them on every boot is safe, including when several replicas
# race. The container binds 0.0.0.0:$PC_PORT and expects TLS to terminate in
# front of it.
set -eu

personal-context-mcp migrate
exec "$@"
