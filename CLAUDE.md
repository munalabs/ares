# CLAUDE.md — Deploy Ares Pentest Profile

## Your Mission

Install the Ares pentest profile on a target machine running Hermes Agent v0.8.0+.
Execute commands remotely via SSH. Stop ONLY at HUMAN ACTION steps.
Run verification after every phase. Fix failures without asking — retry up to 3 times, then report.

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

Ask the user for HOST, USER, and PASSWORD if not provided. This deployment requires:
- Hermes Agent v0.8.0+ already installed (`hermes version`)
- Docker running (`docker ps`)
- Go toolchain (`go version`)
- ~5GB free disk space

---

## PHASE 0: Preflight

```bash
ssh USER@HOST << 'ENDSSH'
echo "=== PREFLIGHT ==="
hermes version || { echo "ERROR: Hermes not installed"; exit 1; }
docker ps > /dev/null || { echo "ERROR: Docker not running"; exit 1; }
go version || { echo "ERROR: Go not installed"; exit 1; }
df -h / | awk 'NR==2{print "Disk free:", $4}'
echo "=== OK ==="
ENDSSH
```

If any check fails, stop and tell the user what's missing.

---

## PHASE 1: System Dependencies

```bash
ssh USER@HOST << 'ENDSSH'
sudo apt-get update -qq
sudo apt-get install -y \
  nmap nikto acl xz-utils android-sdk-platform-tools 2>/dev/null || \
  sudo apt-get install -y nmap nikto acl xz-utils adb

# Python tools
pip3 install sqlmap commix --break-system-packages 2>/dev/null || \
  pip3 install sqlmap commix --user
ENDSSH
```

VERIFY: `ssh USER@HOST "command -v nmap && command -v sqlmap && echo OK"`

---

## PHASE 2: Go Security Tools

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

## PHASE 3: testssl.sh + Wordlists

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

## PHASE 4: Playwright

```bash
ssh USER@HOST << 'ENDSSH'
npm install -g @playwright/mcp@latest 2>/dev/null || sudo npm install -g @playwright/mcp@latest
npx playwright install chromium
npx playwright install-deps chromium
echo "Playwright: done"
ENDSSH
```

---

## PHASE 5: pentest-ai

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

## PHASE 6: OWASP ZAP

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

## PHASE 7: MoBSF

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

## PHASE 8: Iris (Mobile MCP Stack)

Iris provides three FastMCP servers: MoBSF, ADB, and Frida.

```bash
ssh USER@HOST << 'ENDSSH'
pip3 install fastmcp httpx frida frida-tools --break-system-packages 2>/dev/null || \
  pip3 install fastmcp httpx frida frida-tools --user

if [ ! -d ~/tools/iris ]; then
  git clone https://github.com/munalabs/iris.git ~/tools/iris
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

## PHASE 9: Output Directory

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

## PHASE 10: Profile Installation

Clone the repo on the target and install the profile files:

```bash
ssh USER@HOST << 'ENDSSH'
PROFILE_DIR=~/.hermes/profiles/pentest
mkdir -p "$PROFILE_DIR/skills/pentest-orchestrate"

# Clone or update
if [ -d ~/tools/ares ]; then
  git -C ~/tools/ares pull --quiet
else
  git clone https://github.com/munalabs/ares.git ~/tools/ares
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

## PHASE 11: Profile .env

**HUMAN ACTION #1:** Provide the following values:
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

## PHASE 12: Gateway

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

**HUMAN ACTION #2:** Send a message in the pentest forum channel. Confirm the bot responds.

---

## PHASE 13: Full Verification

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

echo "--- Gateway ---"
systemctl --user is-active hermes-gateway-pentest 2>/dev/null || echo "pentest gateway: inactive"

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

## PHASE 14: Smoke Test (Optional)

Test with OWASP Juice Shop — a deliberately vulnerable app:

```bash
ssh USER@HOST 'docker run -d --name juice-shop -p 3001:3000 --rm bkimminich/juice-shop'
sleep 15
ssh USER@HOST 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3001'
```

**HUMAN ACTION #3:** In the pentest forum channel, create a new thread and send:

```
Full web app assessment on http://localhost:3001
OWASP Juice Shop — fully authorized.
Admin: admin@juice-sh.op / admin123
User: jim@juice-sh.op / ncc-1701
Destructive: yes. Go.
```

Expect 5+ validated findings with PoC scripts attached.

Cleanup: `ssh USER@HOST 'docker stop juice-shop'`

---

## HUMAN ACTIONS SUMMARY

| # | Phase | Action |
|---|-------|--------|
| 1 | 11 | Provide Discord bot token, user ID, forum channel ID |
| 2 | 12 | Confirm pentest bot responds in Discord |
| 3 | 14 | Run Juice Shop smoke test (optional) |

---

## Troubleshooting

**Gateway fails to start — missing env vars:**
The service unit needs `EnvironmentFile`. Check Phase 12 patching step.
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
Check volumes in `config.yaml` have YOURUSER replaced with actual username.

**MoBSF crashes immediately:**
Data dir must be owned by uid 9901: `sudo chown -R 9901:9901 ~/services/mobsf`

**Opus 4.6 stream killed mid-reasoning:**
`HERMES_STREAM_STALE_TIMEOUT=900` must be in `profiles/pentest/.env`.
Default 180s is too short — xhigh reasoning is silent for 3-5 minutes before first token.

**delegate_task files lost:**
Delegates run in isolated containers without `/pentest-output` mounted.
Never use `delegate_task` to write deliverables — have it return text, write files yourself.
