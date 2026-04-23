#!/bin/sh
# Hermes container entrypoint — starts gateway + web UI (v0.9.0+)
set -e

# Web UI: bind to all interfaces so it's reachable outside the container.
# Port 9119 is mapped in compose.yml → host port HERMES_WEB_PORT (default 9119).
hermes dashboard --host 0.0.0.0 --port "${HERMES_WEB_PORT:-9119}" --insecure &

# Gateway is PID 1 — receives SIGTERM for clean shutdown
exec hermes gateway
