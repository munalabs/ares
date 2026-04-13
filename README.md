# Ares — Hermes Pentest Profile

A production-grade penetration testing profile for [Hermes Agent](https://github.com/NousResearch/hermes-agent) following the full OWASP Web Security Testing Guide (WSTG) methodology. Runs as an autonomous agent in a Discord forum channel, one thread per engagement.

## What It Does

- **Full OWASP WSTG coverage** — 12 testing phases, 80+ test cases, automated where possible
- **Validated findings only** — every finding requires a working PoC before it lands in the report
- **Mobile testing** — static (MoBSF), dynamic (Frida SSL unpinning, crypto hooks), ADB control
- **Attack chains** — correlates findings into exploitable narratives with CVSS 4.0 scoring
- **Detection engineering** — generates Sigma rules and MITRE ATT&CK Navigator layers for every finding
- **Discord-native** — one forum thread = one engagement, async results as file attachments

## Architecture

```
Discord Forum Thread
        │
        ▼
Hermes Gateway (pentest profile)
        │
        ├── Claude Opus 4.6 (reasoning_effort: xhigh)
        │
        ├── MCP: playwright     — headless Chromium, DOM testing, auth crawl
        ├── MCP: pentest-ai     — 27 tools: recon, ZAP, sqlmap, nuclei, ffuf...
        ├── MCP: gitnexus       — GitHub source analysis
        ├── MCP: mobsf          — mobile static analysis (APK/IPA)
        ├── MCP: adb            — device control, APK extraction
        └── MCP: frida          — dynamic instrumentation, SSL unpinning
```

## Skills

`skills/pentest-orchestrate/` is the master orchestrator. It runs all 7 testing phases and delegates to specialized sub-skills for complex vectors:

| Sub-skill | Coverage |
|-----------|----------|
| `pentest-race-condition` | TOCTOU, concurrent request attacks |
| `pentest-websocket` | Auth, CSWSH, frame injection, subscriptions |
| `pentest-graphql` | Introspection, depth bombs, batching bypass, IDOR via resolvers |
| `pentest-supply-chain` | Exposed manifests, .git, CI/CD files, SRI gaps |
| `pentest-cloud-ssrf` | IMDSv1/v2, GCP, Azure, Kubernetes SA token escalation |
| `pentest-detection-engineering` | Sigma rules, MITRE ATT&CK mapping |
| `pentest-framework-enrichment` | ATT&CK techniques, D3FEND countermeasures, NIST CSF |
| `pentest-attack-navigator` | ATT&CK Navigator JSON layer export |
| `pentest-attack-flow` | Attack flow diagram generation |
| `pentest-d3fend-advisor` | Defensive roadmap per finding |
| `pentest-llm-platform-attacks` | OWASP LLM Top 10, prompt injection, cross-tenant |
| `pentest-creative-edge-cases` | Novel attack surface — polyglots, race+IDOR combos |
| `pentest-report-writer` | Consolidated Markdown + HTML report |
| `pentest-cost-tracking` | Token/API cost accounting per engagement |

## Stack Requirements

| Component | Purpose |
|-----------|---------|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.8.0+ | Orchestrator |
| [pentest-ai](https://github.com/0xSteph/pentest-ai) | Scan tool MCP server |
| [OWASP ZAP](https://www.zaproxy.org/) (Docker) | Web scanner, proxy, spider |
| [MoBSF](https://github.com/MobSF/Mobile-Security-Framework-MobSF) (Docker) | Mobile static analysis |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Browser automation |
| [Iris](https://github.com/munalabs/iris) | Mobile MCP stack (MoBSF + ADB + Frida) |
| Claude Opus 4.6 | Reasoning engine (via Anthropic API) |
| Discord bot | Operator interface |

## Quick Start

### 1. Prerequisites

```bash
# Anthropic API key (OAuth or standard)
# Discord bot token (separate bot from your default Hermes instance)
# Google AI Studio API key (for compression/delegation)
```

### 2. Install

```bash
curl -fsSL https://raw.githubusercontent.com/munalabs/ares/main/install.sh | bash
```

The install script sets up:
- Go security tools: nuclei, ffuf, dalfox, subfinder, httpx
- Python tools: sqlmap, commix, pentest-ai
- System tools: nmap, nikto
- Playwright + Chromium
- testssl.sh
- SecLists wordlists
- Output directory (`~/pentest-output`) with correct ACLs
- ZAP and MoBSF Docker containers
- Iris MCP servers (MoBSF + ADB + Frida)

### 3. Configure

```bash
# Copy profile files into Hermes
mkdir -p ~/.hermes/profiles/pentest/skills/pentest-orchestrate

cp config.yaml ~/.hermes/profiles/pentest/config.yaml
cp SOUL.md ~/.hermes/profiles/pentest/SOUL.md
cp MEMORY.md ~/.hermes/profiles/pentest/MEMORY.md
cp skills/pentest-orchestrate/SKILL.md ~/.hermes/profiles/pentest/skills/pentest-orchestrate/SKILL.md

# Copy env template and fill in your values
cp .env.example ~/.hermes/profiles/pentest/.env
$EDITOR ~/.hermes/profiles/pentest/.env
```

Required env vars:
```bash
DISCORD_BOT_TOKEN=           # pentest-specific bot (separate from default Hermes bot)
DISCORD_ALLOWED_USERS=       # your Discord user ID
DISCORD_FREE_RESPONSE_CHANNELS=  # forum channel ID
ZAP_API_KEY=                 # generated during ZAP setup
ZAP_CONTAINER_IP=            # docker inspect owasp-zap | grep IPAddress
MOBSF_API_KEY=               # from MoBSF startup logs
MOBSF_URL=http://172.17.0.1:8100
HERMES_STREAM_STALE_TIMEOUT=900  # Opus xhigh thinks for 3-5 min silently
```

Edit `config.yaml` and replace `YOURUSER` with your Linux username.

### 4. Gateway

```bash
HERMES_HOME=~/.hermes/profiles/pentest hermes gateway install
systemctl --user enable hermes-gateway-pentest
systemctl --user start hermes-gateway-pentest
```

If the generated service file lacks `EnvironmentFile`:
```bash
SERVICE=~/.config/systemd/user/hermes-gateway-pentest.service
sed -i '/\[Service\]/a EnvironmentFile=/home/YOURUSER/.hermes/profiles/pentest/.env' $SERVICE
systemctl --user daemon-reload && systemctl --user restart hermes-gateway-pentest
```

### 5. Run an Engagement

Create a thread in your pentest forum channel and send:
```
Full web app assessment on https://staging.target.com
Scope: staging.target.com only
Auth: admin@target.com / password123
Test accounts: user1@target.com / pass1, user2@target.com / pass2
Destructive: yes
Go.
```

The agent runs all 7 phases autonomously and delivers a Markdown report + PoC scripts as Discord file attachments.

## Output

All files are written to `~/pentest-output/` (bind-mounted at `/pentest-output/` inside the Docker terminal):

```
/pentest-output/
  report-{target}.md          # full technical report
  report-{target}.html        # browser-friendly version
  pocs/                       # PoC scripts, one per finding
  attack-navigator-{id}.json  # MITRE ATT&CK Navigator layer
  sigma/                      # Sigma detection rules
  mobile-report-{app}.md      # mobile assessment report (if applicable)
  mobile-{app}.tar.gz         # mobile findings bundle
```

## ZAP Note

ZAP MCP addon v0.0.1 alpha is non-functional (hardcodes `127.0.0.1`, enforces HTTPS, ignores config flags). This profile uses ZAP via its REST API directly at `http://$ZAP_CONTAINER_IP:8080/JSON/`. Do not add ZAP to `mcp_servers`.

## Docker Compose

`docker/compose.yml` starts ZAP and MoBSF. The install script starts them via individual `docker run` commands with the correct flags, but compose is provided for reference and easier management:

```bash
cd docker && docker compose up -d
```

## License

MIT. pentest-ai is MIT. GitNexus is PolyForm Noncommercial (personal use only).
