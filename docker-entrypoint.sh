#!/bin/sh
# Persistent-host entrypoint: migrate once, then serve.
#
# The server itself is stateless (StreamableHTTP with no sticky sessions), so
# any host that keeps this container running keeps the MCP endpoint up for
# web, iOS and Android clients alike. No tunnels: the container binds
# 0.0.0.0:$PC_PORT and expects TLS to terminate in front of it (host
# reverse proxy, load balancer, or platform ingress).
#
# Migrations run inside one transaction and are recorded with a content hash,
# so re-running them on every boot is safe, including with several replicas
# racing (all but one will find nothing pending).
set -eu

personal-context-mcp migrate
exec personal-context-mcp serve
