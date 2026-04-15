#!/usr/bin/env bash
# Ares — Hermes Pentest Profile
# Install script: sets up all dependencies for the pentest profile.
# Run as the user who will run Hermes (not root).

set -euo pipefail

TOOLS_DIR="${ARES_TOOLS_DIR:-$HOME/tools}"
USER=$(whoami)

echo "=== Ares Pentest Profile Setup ==="
echo "Tools dir: $TOOLS_DIR"
echo ""

# ─── System packages ───────────────────────────────────────────────────────
echo "=== Installing system packages ==="
sudo apt-get install -y \
  nmap nikto dirb whatweb \
  android-sdk-platform-tools \
  acl curl wget git python3 python3-pip nodejs npm

# ─── Python security tools ─────────────────────────────────────────────────
echo "=== Installing Python tools ==="
pip3 install sqlmap commix fastmcp httpx frida frida-tools --break-system-packages 2>/dev/null || \
  pip3 install sqlmap commix fastmcp httpx frida frida-tools

# ─── Go security tools ─────────────────────────────────────────────────────
echo "=== Installing Go tools ==="
export PATH="$HOME/go/bin:/usr/local/go/bin:$PATH"
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install github.com/ffuf/ffuf/v2@latest
go install github.com/hahwul/dalfox/v2@latest
go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
nuclei -update-templates

# ─── Playwright ────────────────────────────────────────────────────────────
echo "=== Installing Playwright MCP ==="
npm install -g @playwright/mcp@latest
npx playwright install chromium
npx playwright install-deps

# ─── pentest-ai ────────────────────────────────────────────────────────────
echo "=== Installing pentest-ai ==="
git clone https://github.com/0xSteph/pentest-ai.git "$TOOLS_DIR/pentest-ai" 2>/dev/null || \
  (cd "$TOOLS_DIR/pentest-ai" && git pull)
cd "$TOOLS_DIR/pentest-ai"
pip3 install -e . --break-system-packages 2>/dev/null || {
  python3 -m venv .venv && source .venv/bin/activate && pip3 install -e .
}
cd -

# ─── iris (Mobile MCP — MoBSF + ADB + Frida) ──────────────────────────────
echo "=== Installing iris mobile MCP servers ==="
git clone https://github.com/your-org/iris.git "$TOOLS_DIR/iris" 2>/dev/null || \
  (cd "$TOOLS_DIR/iris" && git pull)
cd "$TOOLS_DIR/iris"
chmod +x install.sh && ./install.sh
cd -

# ─── testssl.sh ────────────────────────────────────────────────────────────
echo "=== Installing testssl.sh ==="
git clone --depth 1 https://github.com/drwetter/testssl.sh.git "$TOOLS_DIR/testssl.sh" 2>/dev/null || \
  (cd "$TOOLS_DIR/testssl.sh" && git pull)

# ─── Wordlists ─────────────────────────────────────────────────────────────
echo "=== Downloading wordlists ==="
mkdir -p ~/wordlists
SECLISTS="https://raw.githubusercontent.com/danielmiessler/SecLists/master"
curl -sL "$SECLISTS/Discovery/Web-Content/common.txt" -o ~/wordlists/common.txt
curl -sL "$SECLISTS/Discovery/Web-Content/raft-medium-directories.txt" -o ~/wordlists/raft-medium-directories.txt
curl -sL "$SECLISTS/Discovery/Web-Content/raft-medium-files.txt" -o ~/wordlists/raft-medium-files.txt
curl -sL "$SECLISTS/Fuzzing/SQLi/Generic-SQLi.txt" -o ~/wordlists/sqli-generic.txt
curl -sL "$SECLISTS/Fuzzing/XSS/XSS-Jhaddix.txt" -o ~/wordlists/xss-portswigger.txt

# ─── Pentest output directory ──────────────────────────────────────────────
echo "=== Setting up pentest-output directory ==="
mkdir -p ~/pentest-output && chmod 777 ~/pentest-output
sudo ln -sf ~/pentest-output /pentest-output 2>/dev/null || true
sudo mkdir -p /home/user && sudo ln -sf ~/pentest-output /home/user/pentest-output 2>/dev/null || true
# POSIX ACL — files created by Docker containers (root) readable by current user
sudo setfacl -d -m u:${USER}:rwx ~/pentest-output
sudo setfacl -m u:${USER}:rwx ~/pentest-output

# ─── pentest-ai config ─────────────────────────────────────────────────────
echo "=== Writing pentest-ai config ==="
mkdir -p "$TOOLS_DIR/pentest-ai/config"
cat > "$TOOLS_DIR/pentest-ai/config/pentest-ai.yaml" << 'CONFIG'
llm:
  provider: anthropic
  model: claude-opus-4-6
  temperature: 0.0
agent:
  auto_chain: true
  auto_validate_pocs: true
  auto_generate_detections: true
  hitl_mode: true
scope:
  allowed_targets: []
  excluded_targets: []
  max_depth: 3
reporting:
  format: ["markdown", "html"]
  include_detection_rules: true
  cvss_version: "4.0"
CONFIG

echo ""
echo "=== Done! ==="
echo ""
echo "Next steps:"
echo "  1. Start ZAP + MoBSF:      docker compose -f docker/compose.yml up -d"
echo "  2. Copy profile files:     see README.md for exact paths"
echo "  3. Set env vars:           cp .env.example ~/.hermes/profiles/pentest/.env && edit it"
echo "  4. Update config.yaml:     replace YOURUSER placeholders with your username"
echo "  5. Restart gateway:        systemctl --user restart hermes-gateway-pentest"
