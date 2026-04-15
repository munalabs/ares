# CLAUDE.md — Deploy Ares Pentest Stack

## Your Mission

Deploy a complete Hermes-based pentest workstation from a fresh Ubuntu Server 24.04 LTS machine.
Execute commands remotely via SSH. Stop ONLY at HUMAN ACTION steps.
Run verification after every phase. Fix failures without asking — retry up to 3 times, then report.

**This guide covers everything** — from bare OS to a working pentest agent with Discord interface.
If Hermes is already installed, skip to PHASE 5 (Ares tools).

## SSH Pattern

```bash
# Simple commands
ssh USER@HOST "command"

# Multi-line
ssh USER@HOST << 'ENDSSH'
commands
ENDSSH

# With sshpass
sshpass -p 'PASSWORD' ssh -o StrictHostKeyChecking=no USER@HOST "command"
```

Ask the user for HOST, USER, and PASSWORD if not provided.
Minimum hardware: 4 cores, 8GB RAM, 50GB disk, Ubuntu 22.04+.

---

## PHASE 0: Preflight Check

First, detect what's already installed to skip phases that aren't needed:

```bash
ssh USER@HOST << 'ENDSSH'
echo "=== PREFLIGHT ==="
echo -n "Hermes: "; hermes version 2>/dev/null || echo "NOT INSTALLED"
echo -n "Docker: "; docker --version 2>/dev/null || echo "NOT INSTALLED"
echo -n "Go: "; go version 2>/dev/null || echo "NOT INSTALLED"
echo -n "Node: "; node --version 2>/dev/null || echo "NOT INSTALLED"
echo -n "nuclei: "; command -v nuclei >/dev/null 2>&1 && echo "OK" || echo "NOT INSTALLED"
echo -n "ZAP: "; docker ps 2>/dev/null | grep -q owasp-zap && echo "running" || echo "NOT RUNNING"
echo -n "MoBSF: "; docker ps 2>/dev/null | grep -q mobsf && echo "running" || echo "NOT RUNNING"
df -h / | awk 'NR==2{print "Disk free:", $4}'
echo "=== DONE ==="
ENDSSH
```

Tell the user what was found and which phases you'll skip.

---

## PHASE 1: System Dependencies

```bash
ssh USER@HOST << 'ENDSSH'
sudo apt-get update -qq
sudo apt-get install -y \
  git curl wget unzip build-essential \
  python3 python3-pip python3-venv \
  ca-certificates gnupg lsb-release \
  jq acl xz-utils software-properties-common \
  nmap nikto android-sdk-platform-tools 2>/dev/null || \
  sudo apt-get install -y nmap nikto acl xz-utils adb

# Node.js 22
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
  sudo npm install -g pnpm
fi

# Docker
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker $USER
  echo "NOTE: Log out and back in (or run 'newgrp docker') before continuing."
fi
ENDSSH
```

**HUMAN ACTION #1 (if Docker was just installed):** Run `newgrp docker` on the machine, or log out and back in.

---

## PHASE 2: Go Toolchain

```bash
ssh USER@HOST << 'ENDSSH'
if ! command -v go >/dev/null 2>&1; then
  wget -qO- https://go.dev/dl/go1.23.2.linux-amd64.tar.gz | sudo tar -C /usr/local -xzf -
  echo 'export PATH="/usr/local/go/bin:$HOME/go/bin:$HOME/.local/bin:$PATH"' >> ~/.bashrc
  echo "Go installed."
fi
export PATH="/usr/local/go/bin:$HOME/go/bin:$HOME/.local/bin:$PATH"
go version
ENDSSH
```

---

## PHASE 3: Claude Code + Anthropic Auth

Hermes uses Claude Code's OAuth token for Anthropic API access.

```bash
ssh USER@HOST << 'ENDSSH'
if ! command -v claude >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/anthropics/claude-code/main/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
claude --version
ENDSSH
```

**HUMAN ACTION #2:** Run `claude login` on the target machine. It will print a URL — open it in a browser and authenticate with your Anthropic account. This stores an OAuth token that funds all API calls.

```bash
# Verify token was saved
ssh USER@HOST "python3 -c \"
import json, pathlib, sys
creds = pathlib.Path.home() / '.claude' / '.credentials.json'
if creds.exists():
    d = json.loads(creds.read_text())
    for acct in d.get('accounts', {}).values():
        for k in ['token','oauthToken','oauth_token']:
            if k in acct:
                t = acct[k]
                print(f'Token: {t[:15]}...')
                sys.exit(0)
print('ERROR: token not found')
\""
```

Should print `Token: sk-ant-oat01-...`

---

## PHASE 4: Hermes Agent

```bash
ssh USER@HOST << 'ENDSSH'
export PATH="$HOME/.local/bin:$PATH"
if ! command -v hermes >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

  # Fix shebang if needed (installer sometimes uses system python3, missing venv deps)
  HERMES_BIN="$HOME/.hermes/hermes-agent/hermes"
  VENV_PY="$HOME/.hermes/hermes-agent/venv/bin/python3"
  if head -1 "$HERMES_BIN" | grep -q '/usr/bin/env python3'; then
    sed -i "1s|.*|#!${VENV_PY}|" "$HERMES_BIN"
    echo "Fixed hermes shebang."
  fi
  ln -sf "$HERMES_BIN" "$HOME/.local/bin/hermes"
fi
hermes version
ENDSSH
```

VERIFY: `ssh USER@HOST 'hermes version'` shows v0.8.0+

---

## PHASE 4b: Hermes Base Configuration

**HUMAN ACTION #3:** Provide your API keys:
1. **Google AI Studio** — https://aistudio.google.com/apikey (used for compression + delegation, much cheaper than Opus)
2. **Groq** — https://console.groq.com/keys (free tier, used for memory extraction)

```bash
GOOGLE_API_KEY="KEY_HERE"
GROQ_API_KEY="KEY_HERE"

# Extract OAuth token from Claude Code credentials
ANTHROPIC_TOKEN=$(ssh USER@HOST "python3 -c \"
import json, pathlib, sys
creds = pathlib.Path.home() / '.claude' / '.credentials.json'
d = json.loads(creds.read_text())
for acct in d.get('accounts', {}).values():
    for k in ['token','oauthToken','oauth_token']:
        if k in acct:
            print(acct[k]); sys.exit(0)
print('NOT_FOUND')
\"")

ssh USER@HOST "
mkdir -p ~/.hermes
cat > ~/.hermes/.env << EOF
ANTHROPIC_API_KEY=$ANTHROPIC_TOKEN
GOOGLE_API_KEY=$GOOGLE_API_KEY
GROQ_API_KEY=$GROQ_API_KEY
EOF
chmod 600 ~/.hermes/.env

mkdir -p ~/.hermes/memories
cat > ~/.hermes/config.yaml << 'YAML'
model:
  default: claude-sonnet-4-6
  provider: anthropic

smart_model_routing:
  enabled: true
  max_simple_chars: 160
  max_simple_words: 28
  cheap_model:
    provider: google
    model: gemini-2.5-flash

compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20
  protect_last_n: 20
  summary_model: gemini-2.5-flash
  summary_provider: google

terminal:
  backend: docker
  docker_image: nikolaik/python-nodejs:python3.11-nodejs20
  container_cpu: 2
  container_memory: 8192
  container_persistent: true

security:
  tirith_enabled: true
  tirith_fail_open: false
YAML
echo 'Hermes configured.'
"
```

VERIFY:
```bash
ssh USER@HOST "
source ~/.hermes/.env
curl -s https://api.anthropic.com/v1/messages \
  -H 'Authorization: Bearer \$ANTHROPIC_API_KEY' \
  -H 'anthropic-version: 2023-06-01' \
  -H 'content-type: application/json' \
  -H 'user-agent: claude-cli/1.0 (external, cli)' \
  -H 'x-app: cli' \
  -d '{\"model\":\"claude-haiku-4-5-20251001\",\"max_tokens\":10,\"messages\":[{\"role\":\"user\",\"content\":\"say OK\"}]}' \
  | python3 -c \"import sys,json; print('Anthropic:', json.load(sys.stdin)['content'][0]['text'])\"
"
```

---

## PHASE 5: Pentest System Tools

```bash
ssh USER@HOST << 'ENDSSH'
sudo apt-get install -y nmap nikto acl xz-utils android-sdk-platform-tools 2>/dev/null || \
  sudo apt-get install -y nmap nikto acl xz-utils adb

# Python tools
pip3 install sqlmap commix --break-system-packages 2>/dev/null || \
  pip3 install sqlmap commix --user
ENDSSH
```

VERIFY: `ssh USER@HOST "command -v nmap && command -v sqlmap && echo OK"`

---

## PHASE 6: Go Security Tools

```bash
ssh USER@HOST << 'ENDSSH'
export PATH="/usr/local/go/bin:$HOME/go/bin:$HOME/.local/bin:$PATH"
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install github.com/ffuf/ffuf/v2@latest
go install github.com/hahwul/dalfox/v2@latest
go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
nuclei -update-templates -silent
echo "Go tools: done"
ENDSSH
```

VERIFY: `ssh USER@HOST "export PATH=\$HOME/go/bin:\$PATH && nuclei -version 2>&1 | head -1"`

---

## PHASE 7: testssl.sh + Wordlists

```bash
ssh USER@HOST << 'ENDSSH'
# testssl.sh
[ -d ~/tools/testssl.sh ] || git clone --depth 1 https://github.com/drwetter/testssl.sh.git ~/tools/testssl.sh

# Wordlists
mkdir -p ~/wordlists
[ -f ~/wordlists/common.txt ] || \
  curl -sL "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/common.txt" \
    -o ~/wordlists/common.txt
[ -f ~/wordlists/raft-medium-directories.txt ] || \
  curl -sL "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/raft-medium-directories.txt" \
    -o ~/wordlists/raft-medium-directories.txt
[ -f ~/wordlists/raft-medium-files.txt ] || \
  curl -sL "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/raft-medium-files.txt" \
    -o ~/wordlists/raft-medium-files.txt
[ -f ~/wordlists/sqli-generic.txt ] || \
  curl -sL "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Fuzzing/SQLi/Generic-SQLi.txt" \
    -o ~/wordlists/sqli-generic.txt
[ -f ~/wordlists/xss-portswigger.txt ] || \
  curl -sL "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Fuzzing/XSS/XSS-Jhaddix.txt" \
    -o ~/wordlists/xss-portswigger.txt
echo "Wordlists: done"
ENDSSH
```

---

## PHASE 8: Playwright

```bash
ssh USER@HOST << 'ENDSSH'
npm install -g @playwright/mcp@latest 2>/dev/null || sudo npm install -g @playwright/mcp@latest
npx playwright install chromium
npx playwright install-deps chromium
echo "Playwright: done"
ENDSSH
```

---

## PHASE 9: pentest-ai

```bash
ssh USER@HOST << 'ENDSSH'
if [ ! -d ~/tools/pentest-ai ]; then
  git clone https://github.com/0xSteph/pentest-ai.git ~/tools/pentest-ai
fi
cd ~/tools/pentest-ai

# Install deps into system Python (required — Hermes spawns /usr/bin/python3)
pip3 install -e . --break-system-packages 2>/dev/null || {
  # Fallback: install deps system-wide without editable mode
  pip3 install aiosqlite fastmcp anthropic --break-system-packages
  # Add to PYTHONPATH instead
  echo "export PYTHONPATH=\"/home/$USER/tools/pentest-ai:\$PYTHONPATH\"" >> ~/.bashrc
}

mkdir -p ~/tools/pentest-ai/config
cat > ~/tools/pentest-ai/config/pentest-ai.yaml << 'EOF'
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
EOF
echo "pentest-ai: done"
ENDSSH
```

VERIFY: `ssh USER@HOST "/usr/bin/python3 -c 'import aiosqlite, fastmcp; print(\"deps OK\")'"`

---

## PHASE 10: OWASP ZAP

```bash
ssh USER@HOST << 'ENDSSH'
if docker ps --format '{{.Names}}' | grep -q '^owasp-zap$'; then
  echo "ZAP already running"
else
  ZAP_API_KEY=$(openssl rand -hex 16)
  mkdir -p ~/zap-data
  docker pull ghcr.io/zaproxy/zaproxy:stable
  docker run -d --name owasp-zap \
    -p 8090:8080 \
    -v ~/zap-data:/zap/wrk \
    --restart unless-stopped \
    ghcr.io/zaproxy/zaproxy:stable \
    zap.sh -daemon -host 0.0.0.0 -port 8080 \
    -config api.key="$ZAP_API_KEY" \
    -config api.addrs.addr.name=".*" \
    -config api.addrs.addr.regex=true
  sleep 15

  # Store key
  grep -q ZAP_API_KEY ~/.hermes/.env 2>/dev/null && \
    sed -i "s/ZAP_API_KEY=.*/ZAP_API_KEY=$ZAP_API_KEY/" ~/.hermes/.env || \
    echo "ZAP_API_KEY=$ZAP_API_KEY" >> ~/.hermes/.env

  ZAP_IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' owasp-zap)
  grep -q ZAP_CONTAINER_IP ~/.hermes/.env 2>/dev/null && \
    sed -i "s/ZAP_CONTAINER_IP=.*/ZAP_CONTAINER_IP=$ZAP_IP/" ~/.hermes/.env || \
    echo "ZAP_CONTAINER_IP=$ZAP_IP" >> ~/.hermes/.env

  echo "ZAP started. API key stored."
fi
ENDSSH
```

VERIFY:
```bash
ssh USER@HOST 'source ~/.hermes/.env && curl -s "http://localhost:8090/JSON/core/view/version/?apikey=$ZAP_API_KEY" | python3 -c "import sys,json; print(\"ZAP:\", json.load(sys.stdin)[\"version\"])"'
```

---

## PHASE 11: MoBSF

```bash
ssh USER@HOST << 'ENDSSH'
if docker ps --format '{{.Names}}' | grep -q '^mobsf$'; then
  echo "MoBSF already running"
else
  mkdir -p ~/services/mobsf
  sudo chown -R 9901:9901 ~/services/mobsf 2>/dev/null || true

  docker pull opensecurity/mobile-security-framework-mobsf:latest
  docker run -d --name mobsf \
    -p 8100:8000 \
    -v ~/services/mobsf:/home/mobsf/.MobSF \
    --restart unless-stopped \
    opensecurity/mobile-security-framework-mobsf:latest
  sleep 20

  MOBSF_KEY=$(docker logs mobsf 2>&1 | grep -oP 'REST API Key:\s*\K\S+' | tail -1)
  [ -z "$MOBSF_KEY" ] && MOBSF_KEY=$(openssl rand -hex 32)

  grep -q MOBSF_API_KEY ~/.hermes/.env 2>/dev/null && \
    sed -i "s/MOBSF_API_KEY=.*/MOBSF_API_KEY=$MOBSF_KEY/" ~/.hermes/.env || \
    echo "MOBSF_API_KEY=$MOBSF_KEY" >> ~/.hermes/.env

  grep -q MOBSF_URL ~/.hermes/.env 2>/dev/null || \
    echo "MOBSF_URL=http://172.17.0.1:8100" >> ~/.hermes/.env

  echo "MoBSF started. Key: $MOBSF_KEY"
fi
ENDSSH
```

VERIFY: `ssh USER@HOST 'source ~/.hermes/.env && curl -s -o /dev/null -w "%{http_code}" "$MOBSF_URL/api/v1/scans?page=1" -H "Authorization: $MOBSF_API_KEY"'`
Should return `200`.

---

## PHASE 12: Iris (Mobile MCP Stack)

Iris provides three FastMCP servers: MoBSF, ADB, and Frida.

```bash
ssh USER@HOST << 'ENDSSH'
pip3 install fastmcp httpx frida frida-tools --break-system-packages 2>/dev/null || \
  pip3 install fastmcp httpx frida frida-tools --user

if [ ! -d ~/tools/iris ]; then
  git clone https://github.com/your-org/iris.git ~/tools/iris
fi

# Verify syntax
for f in ~/tools/iris/mobsf.py ~/tools/iris/adb.py ~/tools/iris/frida.py; do
  python3 -c "import ast; ast.parse(open('$f').read()); print('$f: OK')"
done
ENDSSH
```

**Optional — frida-server binaries for dynamic analysis:**
```bash
ssh USER@HOST << 'ENDSSH'
FRIDA_VERSION=$(python3 -m pip show frida 2>/dev/null | grep Version | awk '{print $2}')
mkdir -p ~/tools/frida-server

# ARM64 (physical devices — most modern Android phones)
curl -sL "https://github.com/frida/frida/releases/download/${FRIDA_VERSION}/frida-server-${FRIDA_VERSION}-android-arm64.xz" \
  -o /tmp/frida-arm64.xz && xz -d /tmp/frida-arm64.xz && \
  mv /tmp/frida-arm64 ~/tools/frida-server/frida-server-android-arm64 && \
  chmod +x ~/tools/frida-server/frida-server-android-arm64 && \
  echo "frida-server arm64: OK"

# x86_64 (emulators)
curl -sL "https://github.com/frida/frida/releases/download/${FRIDA_VERSION}/frida-server-${FRIDA_VERSION}-android-x86_64.xz" \
  -o /tmp/frida-x86.xz && xz -d /tmp/frida-x86.xz && \
  mv /tmp/frida-x86 ~/tools/frida-server/frida-server-android-x86_64 && \
  chmod +x ~/tools/frida-server/frida-server-android-x86_64 && \
  echo "frida-server x86_64: OK"
ENDSSH
```

---

## PHASE 13: Output Directory

```bash
ssh USER@HOST << 'ENDSSH'
mkdir -p ~/pentest-output
chmod 777 ~/pentest-output

# ACL: Docker containers run as root; files must be readable by the host user
sudo apt-get install -y acl -qq
sudo setfacl -d -m u:$USER:rwx ~/pentest-output
sudo setfacl -m u:$USER:rwx ~/pentest-output

# Host symlinks so gateway finds /pentest-output/ and /home/user/pentest-output/
sudo ln -sf ~/pentest-output /pentest-output 2>/dev/null || true
sudo mkdir -p /home/user
sudo ln -sf ~/pentest-output /home/user/pentest-output 2>/dev/null || true

echo "Output dir: done"
ENDSSH
```

---

## PHASE 14: Profile Installation

Clone the repo on the target and install the profile files:

```bash
ssh USER@HOST << 'ENDSSH'
PROFILE_DIR=~/.hermes/profiles/pentest
mkdir -p "$PROFILE_DIR/skills/pentest-orchestrate"

# Clone or update
if [ -d ~/tools/ares ]; then
  git -C ~/tools/ares pull --quiet
else
  git clone https://github.com/your-org/ares.git ~/tools/ares
fi

# Install profile files
cp ~/tools/ares/config.yaml "$PROFILE_DIR/config.yaml"
cp ~/tools/ares/SOUL.md "$PROFILE_DIR/SOUL.md"
cp ~/tools/ares/MEMORY.md "$PROFILE_DIR/MEMORY.md"
cp ~/tools/ares/skills/pentest-orchestrate/SKILL.md \
   "$PROFILE_DIR/skills/pentest-orchestrate/SKILL.md"

# Patch YOURUSER placeholder with actual username
sed -i "s/YOURUSER/$USER/g" "$PROFILE_DIR/config.yaml"

echo "Profile files installed."
ENDSSH
```

---

## PHASE 15: Profile .env

**HUMAN ACTION #4:** Provide the following values:
1. **DISCORD_BOT_TOKEN** — bot token for a *separate* Discord application (not your default Hermes bot). Create at https://discord.com/developers/applications.
2. **DISCORD_ALLOWED_USERS** — your Discord user ID (right-click your profile → Copy User ID)
3. **DISCORD_FREE_RESPONSE_CHANNELS** — the forum channel ID where engagements will run

```bash
# Fill in values from human input
DISCORD_BOT_TOKEN="TOKEN_HERE"
DISCORD_ALLOWED_USERS="USER_ID_HERE"
DISCORD_FREE_RESPONSE_CHANNELS="CHANNEL_ID_HERE"

ssh USER@HOST "
source ~/.hermes/.env

cat > ~/.hermes/profiles/pentest/.env << EOF
DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN
DISCORD_ALLOWED_USERS=$DISCORD_ALLOWED_USERS
DISCORD_FREE_RESPONSE_CHANNELS=$DISCORD_FREE_RESPONSE_CHANNELS
ZAP_API_KEY=\$(grep ZAP_API_KEY ~/.hermes/.env | cut -d= -f2)
ZAP_CONTAINER_IP=\$(grep ZAP_CONTAINER_IP ~/.hermes/.env | cut -d= -f2)
MOBSF_API_KEY=\$(grep MOBSF_API_KEY ~/.hermes/.env | cut -d= -f2)
MOBSF_URL=http://172.17.0.1:8100
HERMES_STREAM_STALE_TIMEOUT=900
EOF
chmod 600 ~/.hermes/profiles/pentest/.env
echo 'Profile .env written.'
"
```

---

## PHASE 16: SwarmClaw Web UI

SwarmClaw is the multi-engagement web UI. It connects to the Hermes HTTP API and gives each pentester a persistent session dashboard — no Discord required.

```bash
# Install SwarmClaw
ssh USER@HOST 'npm install -g @swarmclawai/swarmclaw'

# Create systemd user service
ssh USER@HOST "cat > ~/.config/systemd/user/swarmclaw.service << 'UNIT'
[Unit]
Description=SwarmClaw Multi-Agent Runtime
After=network.target hermes-gateway-pentest.service

[Service]
Type=simple
Environment=PATH=/home/$USER/.local/npm-global/bin:/home/$USER/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$(which swarmclaw || echo /home/$USER/.local/npm-global/bin/swarmclaw) start
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
UNIT
"

ssh USER@HOST '
# Enable Hermes HTTP API for the pentest profile
grep -q API_SERVER_ENABLED ~/.hermes/profiles/pentest/.env 2>/dev/null || \
  echo "API_SERVER_ENABLED=true" >> ~/.hermes/profiles/pentest/.env
grep -q API_SERVER_PORT ~/.hermes/profiles/pentest/.env 2>/dev/null || \
  echo "API_SERVER_PORT=8643" >> ~/.hermes/profiles/pentest/.env

systemctl --user daemon-reload
systemctl --user enable swarmclaw
systemctl --user start swarmclaw
sleep 5
systemctl --user is-active swarmclaw
'
```

VERIFY: `ssh USER@HOST 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3456'` should return 200.

Open `http://HOST:3456` to access the SwarmClaw dashboard.

---

## PHASE 17: Gateway

```bash
ssh USER@HOST 'HERMES_HOME=~/.hermes/profiles/pentest hermes gateway install'

# Ensure EnvironmentFile is in the service unit (required for profile vars)
ssh USER@HOST "
SERVICE=\$HOME/.config/systemd/user/hermes-gateway-pentest.service
if [ -f \"\$SERVICE\" ] && ! grep -q EnvironmentFile \"\$SERVICE\"; then
  sed -i '/\[Service\]/a EnvironmentFile=/home/\$USER/.hermes/profiles/pentest/.env' \"\$SERVICE\"
  sed -i \"s|/home/\\\$USER/|/home/$USER/|g\" \"\$SERVICE\"
  echo 'Patched EnvironmentFile into service unit.'
fi
systemctl --user daemon-reload
systemctl --user enable hermes-gateway-pentest
systemctl --user start hermes-gateway-pentest
sleep 5
systemctl --user is-active hermes-gateway-pentest
"
```

VERIFY: `ssh USER@HOST 'tail -5 ~/.hermes/profiles/pentest/logs/gateway.log'`
Should show: `✓ discord connected as YourBotName#XXXX`

**HUMAN ACTION #5:** Send a message in the pentest forum channel. Confirm the bot responds.

---

## PHASE 18: Full Verification

```bash
ssh USER@HOST << 'ENDSSH'
echo "=== ARES VERIFICATION ==="

echo "--- Docker services ---"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'NAMES|owasp-zap|mobsf'

echo "--- Security tools ---"
export PATH="$HOME/go/bin:/usr/local/go/bin:$PATH"
for tool in nmap sqlmap nuclei ffuf dalfox subfinder nikto; do
  echo -n "$tool: "; command -v $tool >/dev/null 2>&1 && echo "OK" || echo "MISSING"
done

echo "--- Python tools ---"
/usr/bin/python3 -c "import aiosqlite, fastmcp; print('pentest-ai deps: OK')" 2>/dev/null || echo "pentest-ai deps: MISSING"
/usr/bin/python3 -c "import frida; print('frida: OK')" 2>/dev/null || echo "frida: MISSING"

echo "--- Iris MCP servers ---"
for f in mobsf adb frida; do
  [ -f ~/tools/iris/$f.py ] && echo "iris/$f.py: OK" || echo "iris/$f.py: MISSING"
done

echo "--- ZAP ---"
source ~/.hermes/.env
curl -s "http://localhost:8090/JSON/core/view/version/?apikey=$ZAP_API_KEY" \
  | python3 -c "import sys,json; print('ZAP:', json.load(sys.stdin).get('version','NOT RUNNING'))" 2>/dev/null \
  || echo "ZAP: not reachable"

echo "--- MoBSF ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: $MOBSF_API_KEY" \
  "$MOBSF_URL/api/v1/scans?page=1" 2>/dev/null)
[ "$STATUS" = "200" ] && echo "MoBSF: OK" || echo "MoBSF: $STATUS"

echo "--- Gateway + UI ---"
systemctl --user is-active hermes-gateway-pentest 2>/dev/null || echo "pentest gateway: inactive"
systemctl --user is-active swarmclaw 2>/dev/null || echo "swarmclaw: inactive"
curl -s -o /dev/null -w "SwarmClaw HTTP: %{http_code}\n" http://localhost:3456 2>/dev/null || echo "SwarmClaw: not reachable"

echo "--- Profile files ---"
for f in config.yaml SOUL.md MEMORY.md; do
  [ -f ~/.hermes/profiles/pentest/$f ] && echo "$f: OK" || echo "$f: MISSING"
done
[ -f ~/.hermes/profiles/pentest/skills/pentest-orchestrate/SKILL.md ] && \
  echo "SKILL.md: OK" || echo "SKILL.md: MISSING"

echo "=== DONE ==="
ENDSSH
```

---

## PHASE 19: Smoke Test (Optional)

Test with OWASP Juice Shop — a deliberately vulnerable app:

```bash
ssh USER@HOST 'docker run -d --name juice-shop -p 3001:3000 --rm bkimminich/juice-shop'
sleep 15
ssh USER@HOST 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3001'
```

**HUMAN ACTION #6:** Start a new engagement:

**Via SwarmClaw (no Discord needed):** Open `http://HOST:3456` → new conversation → send:

**Via Discord:** Create a new thread in the pentest forum channel and send:

```
Full web app assessment on http://172.17.0.1:3001
OWASP Juice Shop — fully authorized.
Admin: admin@juice-sh.op / admin123
User: jim@juice-sh.op / ncc-1701
Destructive: yes. Go.
```

Expect 5+ validated findings with PoC scripts.

Cleanup: `ssh USER@HOST 'docker stop juice-shop'`

---

## HUMAN ACTIONS SUMMARY

| # | Phase | Action |
|---|-------|--------|
| 1 | 1 | Run `newgrp docker` if Docker was just installed |
| 2 | 3 | Run `claude login` → complete browser OAuth |
| 3 | 4b | Provide Google AI Studio + Groq API keys |
| 4 | 15 | Provide Discord bot token, user ID, forum channel ID |
| 5 | 16 | Confirm pentest bot responds in Discord |
| 6 | 18 | Run Juice Shop smoke test (optional) |

---

## Troubleshooting

**Gateway fails to start — missing env vars:**
The service unit needs `EnvironmentFile`. Check Phase 16 patching step.
`journalctl --user -u hermes-gateway-pentest -n 30`

**pentest-ai MCP times out silently:**
Ensure `timeout: 1800` is in the `pentest-ai` entry of `config.yaml`.
Empty error message (`MCP tool call failed:` with nothing after the colon) = timeout.

**ZAP unreachable from inside Docker terminal:**
Use `ZAP_CONTAINER_IP` (the Docker bridge IP), not `localhost:8090`.
`localhost` inside the container points to the container itself, not the host.

**Files written to /pentest-output not found by gateway:**
Check symlinks: `ls -la /pentest-output` and `ls -la /home/user/pentest-output`
Check ACLs: `getfacl ~/pentest-output`
Check volumes in `config.yaml` have YOURUSER replaced with actual username (done automatically in Phase 14).

**MoBSF crashes immediately:**
Data dir must be owned by uid 9901: `sudo chown -R 9901:9901 ~/services/mobsf`

**Opus 4.6 stream killed mid-reasoning:**
`HERMES_STREAM_STALE_TIMEOUT=900` must be in `profiles/pentest/.env`.
Default 180s is too short — xhigh reasoning is silent for 3-5 minutes before first token.

**delegate_task files lost:**
Delegates run in isolated containers without `/pentest-output` mounted.
Never use `delegate_task` to write deliverables — have it return text, write files yourself.
