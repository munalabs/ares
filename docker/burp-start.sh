#!/usr/bin/env bash
# burp-start.sh — Bridge Burp Pro MCP + Proxy into the Ares Docker stack
#
# Requires Burp Pro running with:
#   - MCP extension enabled (BApp Store) listening on 127.0.0.1:9876
#   - Proxy listener on 192.168.64.1:8091 (for traffic interception)
#
# Usage:
#   ./burp-start.sh         — start bridges + enable Burp MCP in hermes
#   ./burp-start.sh --stop  — stop bridges

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}▶${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*"; }
die()     { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

[[ -d /opt/homebrew/bin ]] && export PATH="/opt/homebrew/bin:$PATH"

DOCKER_HOST_IP=$(grep -E "^ANDROID_ADB_SERVER_HOST=" .env 2>/dev/null | cut -d= -f2 || echo "192.168.64.1")
BURP_MCP_PORT=9876
BURP_MCP_BRIDGE=9877
BURP_PROXY_PORT=8091

if [[ "${1:-}" == "--stop" ]]; then
    pkill -f "burp-proxy.py" 2>/dev/null && success "MCP proxy stopped" || warn "MCP proxy not running"
    info "To fully disable: rebuild hermes or set enabled: false in config.yaml"
    exit 0
fi

command -v python3 >/dev/null 2>&1 || die "python3 not found"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROXY_SCRIPT="${SCRIPT_DIR}/burp-proxy.py"
[ -f "$PROXY_SCRIPT" ] || die "burp-proxy.py not found at $PROXY_SCRIPT"

echo
echo "────────────────────────────────────────────────────────────"
echo "  Ares — Burp Pro Integration"
echo "  Docker host IP: ${DOCKER_HOST_IP}"
echo "────────────────────────────────────────────────────────────"
echo

# ── Verify Burp MCP is running ────────────────────────────────────────────────
info "Checking Burp MCP server (127.0.0.1:${BURP_MCP_PORT})..."
HTTP_CODE=$(curl -s --max-time 3 -o /dev/null -w "%{http_code}" \
    -I http://127.0.0.1:${BURP_MCP_PORT}/ 2>/dev/null || echo "000")
[[ "$HTTP_CODE" == "200" ]] \
    && success "Burp MCP: running (HTTP $HTTP_CODE)" \
    || die "Burp MCP not reachable on 127.0.0.1:${BURP_MCP_PORT} (got $HTTP_CODE). Start Burp Pro and enable the MCP extension."

# ── Verify Burp Proxy listener ────────────────────────────────────────────────
info "Checking Burp Proxy listener (${DOCKER_HOST_IP}:${BURP_PROXY_PORT})..."
curl -s --max-time 3 -o /dev/null -w "%{http_code}" \
    http://${DOCKER_HOST_IP}:${BURP_PROXY_PORT}/ 2>/dev/null | grep -q "200" \
    && success "Burp Proxy: reachable at ${DOCKER_HOST_IP}:${BURP_PROXY_PORT}" \
    || warn "Burp Proxy not reachable — traffic interception won't work. Add a proxy listener on ${DOCKER_HOST_IP}:${BURP_PROXY_PORT} in Burp."

# ── MCP reverse proxy (rewrites Host header for Burp) ─────────────────────────
info "MCP proxy (${DOCKER_HOST_IP}:${BURP_MCP_BRIDGE} → 127.0.0.1:${BURP_MCP_PORT})..."
pkill -f "burp-proxy.py" 2>/dev/null || true
sleep 1
nohup python3 "$PROXY_SCRIPT" \
    --bind "${DOCKER_HOST_IP}" \
    --port "${BURP_MCP_BRIDGE}" \
    --upstream "127.0.0.1:${BURP_MCP_PORT}" \
    >/tmp/ares-burp-proxy.log 2>&1 &
PROXY_PID=$!
sleep 2
kill -0 "$PROXY_PID" 2>/dev/null \
    && success "MCP proxy: PID $PROXY_PID" \
    || die "burp-proxy.py failed — check /tmp/ares-burp-proxy.log"

# ── Verify bridge from hermes container ──────────────────────────────────────
info "Verifying MCP from hermes container..."
RESULT=$(docker exec ares-hermes curl -s --max-time 3 \
    -H "Host: 127.0.0.1:${BURP_MCP_PORT}" \
    -o /dev/null -w "%{http_code}" \
    http://${DOCKER_HOST_IP}:${BURP_MCP_BRIDGE}/ 2>&1 || true)
[[ "$RESULT" == "200" ]] \
    && success "Hermes → Burp MCP: OK (HTTP $RESULT)" \
    || warn "Hermes → Burp MCP: HTTP $RESULT. Check bridge and Burp."

# ── Restart hermes so it picks up Burp MCP ───────────────────────────────────
info "Restarting hermes to register Burp MCP..."
docker restart ares-hermes >/dev/null \
    && success "hermes restarted — Burp MCP registered" \
    || warn "Could not restart hermes — run: docker restart ares-hermes"

echo
echo "────────────────────────────────────────────────────────────"
echo "  Burp integration ready."
echo "  MCP proxy:   ${DOCKER_HOST_IP}:${BURP_MCP_BRIDGE} → 127.0.0.1:${BURP_MCP_PORT} (Host rewritten)"
echo "  Proxy:       ${DOCKER_HOST_IP}:${BURP_PROXY_PORT}"
echo "  Bridges stop when this terminal closes."
echo "  Log:         /tmp/ares-burp-proxy.log
  To stop:     ./burp-start.sh --stop"
echo "────────────────────────────────────────────────────────────"
echo
