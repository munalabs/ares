# Ares — Autonomous Pentest Agent

Ares is a penetration testing profile for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It turns a bare server or local workstation into a fully autonomous pentest platform — operated via Discord, a web UI ([SwarmClaw](https://swarmclaw.ai)), or both simultaneously.

You send a target URL and credentials. The agent runs a full OWASP WSTG assessment, validates every finding with a working proof of concept, and delivers a Markdown/HTML/PDF report with PoC scripts. No manual steps during the engagement.

---

## What It Does

- **Full OWASP WSTG coverage** — 13 testing phases, 80+ test cases across recon, auth, authz, injection, business logic, client-side, API, and mobile
- **Validated findings only** — every finding requires a working PoC before it lands in the report. No theoretical vulnerabilities.
- **Per-engagement isolation** — each run creates `/pentest-output/{target}_{timestamp}/` so parallel engagements never conflict
- **Attack chains** — correlates individual findings into exploitable end-to-end scenarios with CVSS 4.0 scoring
- **Detection engineering** — Sigma rules and MITRE ATT&CK Navigator layers for every validated finding
- **Mobile testing** — static analysis (MoBSF), dynamic instrumentation (Frida SSL unpinning, crypto hooks), device control (ADB)
- **Memory across engagements** — Hindsight extracts findings and patterns after each session, building institutional knowledge over time
- **Dual interface** — Discord (one forum thread per engagement) or SwarmClaw web UI (local, no bot required); both can run simultaneously

---

## Architecture

```
Discord Forum Thread          SwarmClaw Web UI
(one thread = one engagement) (http://localhost:3456)
        │                             │
        └─────────────┬───────────────┘
                      ▼
Hermes Gateway — pentest profile (HTTP API :8643 + optional Discord)
        │
        ├── Claude Sonnet 4.6 (orchestrator — phases 0–2, tool execution, report assembly)
        │       │
        │       └── Claude Opus 4.6 delegate_task (reasoning_effort: xhigh)
        │               └── phases 3–5 analysis, attack chain building, CVSS scoring
        │
        ├── Haiku 4.5 (auto-routed for trivial turns — JSON parsing, format ops)
        │
        ├── MCP: playwright      — headless Chromium, DOM testing, authenticated crawl
        ├── MCP: pentest-ai      — 27 tools: ZAP, sqlmap, nuclei, ffuf, nmap, dalfox...
        ├── MCP: gitnexus        — GitHub source analysis, secrets, exposed config
        ├── MCP: mobsf           — mobile static analysis (APK/IPA)
        ├── MCP: adb             — Android device control, APK extraction
        └── MCP: frida           — dynamic instrumentation, SSL unpinning, crypto hooks
```

**Model routing** is tiered by cost and reasoning requirement:

| Model | Role | Triggers |
|-------|------|----------|
| Sonnet 4.6 | Default orchestrator | All phases — coordination, tool execution, parsing, report writing |
| Opus 4.6 | Deep analysis | Exploit confirmation, attack chain building, CVSS scoring |
| Haiku 4.5 | Trivial turns | Auto-routed for JSON extraction, format conversions, simple lookups |

This routing cuts cost ~43% vs full Opus without quality loss on tool execution or report writing.

---

## Stack

| Component | Purpose | Where it runs |
|-----------|---------|---------------|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.8.0+ | Orchestrator, HTTP API, Discord gateway, skill engine | Host / Docker |
| [SwarmClaw](https://swarmclaw.ai) | Web UI — multi-engagement dashboard, parallel session management | Docker container |
| [pentest-ai](https://github.com/0xSteph/pentest-ai) | MCP server — 27 scan/exploit tools | Host (spawned by Hermes) |
| [OWASP ZAP](https://www.zaproxy.org/) | Web scanner, spider, passive scanner | Docker container (per engagement) |
| [MoBSF](https://github.com/MobSF/Mobile-Security-Framework-MobSF) | Mobile static analysis (APK/IPA/APPX) | Docker container |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Headless Chromium — DOM testing, auth crawl | Host (spawned by Hermes) |
| [Iris](https://github.com/munalabs/iris) | MCP servers for MoBSF, ADB, Frida | Host (spawned by Hermes) |
| [Claude Code](https://claude.ai/code) | Anthropic OAuth token source | Host |
| [Hindsight](https://github.com/NousResearch/hindsight) | Persistent memory — cross-session learning | Host (systemd user service) |
| sqlmap, nuclei, ffuf, dalfox | Injection/scan CLI tools | Host (called by pentest-ai) |
| nmap, nikto, subfinder, httpx | Recon CLI tools | Host (called by pentest-ai) |
| testssl.sh | TLS/SSL analysis | Host |
| Discord bot (optional) | Alternative operator interface | Connects to Discord gateway |

**Minimum hardware:** 4 cores, 8GB RAM, 50GB disk, Ubuntu 22.04+

**Recommended:** 8+ cores, 16GB+ RAM, 200GB SSD (for ZAP + MoBSF + concurrent sessions)

---

## Skills

`skills/pentest-orchestrate/` is the master orchestrator. It runs all 13 phases and delegates specialized testing to sub-skills:

| Sub-skill | Coverage |
|-----------|----------|
| `pentest-race-condition` | TOCTOU, concurrent request attacks |
| `pentest-websocket` | Auth bypass, CSWSH, frame injection, subscriptions |
| `pentest-graphql` | Introspection, depth bombs, batching bypass, IDOR via resolvers |
| `pentest-supply-chain` | Exposed manifests, .git exposure, CI/CD files, SRI gaps |
| `pentest-cloud-ssrf` | IMDSv1/v2, GCP metadata, Azure IMDS, Kubernetes SA token escalation |
| `pentest-llm-platform-attacks` | OWASP LLM Top 10, prompt injection, cross-tenant data leakage |
| `pentest-creative-edge-cases` | Novel attack surface — polyglots, race+IDOR combos, parser differentials |
| `pentest-detection-engineering` | Sigma rules per finding, MITRE ATT&CK mapping |
| `pentest-framework-enrichment` | ATT&CK techniques, D3FEND countermeasures, NIST CSF subcategories |
| `pentest-attack-navigator` | MITRE ATT&CK Navigator JSON layer export |
| `pentest-attack-flow` | Attack flow diagram generation |
| `pentest-d3fend-advisor` | Defensive roadmap per finding |
| `pentest-report-writer` | Consolidated Markdown + HTML + PDF report |

---

## Testing Phases

The orchestrator runs these phases sequentially per engagement:

| Phase | Coverage |
|-------|----------|
| 0 | Scope lock, per-engagement output dir, ZAP context setup |
| 1 | Recon — nmap, nuclei, subfinder, ZAP spider, AJAX spider, authenticated Playwright crawl, ffuf content discovery |
| 2 | Passive analysis — ZAP alerts, security headers, JS source analysis, cookie flags, client-side storage |
| 3 | Auth & authz — IDOR across all ID parameters, broken function-level auth, mass assignment, JWT attacks, CSRF |
| 4 | Injection — SQLi (sqlmap), XSS (dalfox), DOM XSS (Playwright), SSRF, SSTI, command injection, XXE (file upload + XML endpoints), file upload abuse |
| 5 | Business logic — race conditions, rate limiting, workflow bypass, WebSocket auth, CORS |
| 6 | Validation + chain building — Opus confirms every finding, builds attack chains, scores CVSS 4.0 |
| 7 | Report — executive summary, per-finding detail with PoC + evidence, Sigma rules, ATT&CK layer, PDF delivery |
| 8 | Error handling — stack traces, framework debug pages |
| 9 | Cryptography — TLS config (testssl.sh), weak hashing, padding oracle |
| 10 | Client-side — DOM XSS sinks, postMessage, XSSI, clickjacking |
| 11 | API — REST rate limiting, BOLA, GraphQL introspection, WebSocket |
| 12 | Business logic deep dive — multi-step workflow bypass, negative quantities, TOCTOU |
| 13 | Mobile — MoBSF static analysis, Frida dynamic (SSL unpinning, crypto hooks), ADB device testing |

---

## Output

Every engagement writes to an isolated directory:

```
/pentest-output/
  {target-slug}_{YYYYMMDD_HHMMSS}/
    final-report.html           — full technical report, browser-ready
    final-report.pdf            — same report as PDF
    evidence/
      F-01_sqli_response.txt    — raw request/response for each finding
      phase-2-summary.md        — phase summaries (compaction resilience)
    screenshots/
      F-05_xss.png              — browser evidence for client-side findings
    pocs/
      F-01_sqli.sh              — standalone PoC script per finding
  {target-slug}_{timestamp}-FINAL.tar.gz    — full bundle for archival
```

The tarball and HTML report are attached directly to the Discord thread.

---

## Memory

Hindsight runs as a persistent daemon, extracting findings and attack patterns after each engagement into a vector store. This means:

- Recurring vulnerabilities across multiple targets are surfaced ("this same JWT misconfiguration appeared in 3 engagements")
- Effective payloads are remembered across sessions
- The agent gets better at prioritizing high-value test vectors over time

Hindsight uses Groq free tier (llama-3.3-70b-versatile) for memory extraction — no additional cost beyond the free quota.

---

## Quick Start

Three deployment paths depending on your use case:

| Deployment | Interface | Who it's for |
|------------|-----------|--------------|
| **Option A** — Docker Compose | SwarmClaw web UI | Teams, local pentester workstations |
| **Option B** — Docker Compose + Discord | Discord forum threads | Personal / on-call security team |
| **Option C** — Bare-metal via Claude Code | Discord + SwarmClaw | Production, Hindsight memory, physical device testing |

---

### Option A: Docker Compose — Local / Team (No Discord)

The default Docker Compose deployment. No Discord bot needed. SwarmClaw runs at `http://localhost:3456` as the multi-engagement web UI. Each conversation in SwarmClaw is an isolated engagement session.

**Prerequisites:** Docker Engine, Docker Compose v2, Anthropic OAuth token (`claude login`). No root required — your user needs to be in the `docker` group (`sudo usermod -aG docker $USER`).

```bash
git clone https://github.com/munalabs/ares.git
cd ares/docker
./setup.sh
```

The script runs as your normal user. It prompts for your Anthropic token (and optionally Discord), auto-generates ZAP and MoBSF API keys, builds images, starts the stack, seeds SwarmClaw with the Ares Pentest agent, and verifies everything. First run takes ~5 minutes (npm install on first SwarmClaw start).

Open **http://localhost:3456** → start a new conversation → send your engagement brief.

---

### Option B: Docker Compose + Discord

Run `setup.sh` and answer **y** to the Discord prompt, or add credentials to `.env` after the fact:

```bash
# Add to .env (uncomment and fill in):
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_ALLOWED_USERS=your-discord-user-id
DISCORD_FREE_RESPONSE_CHANNELS=your-forum-channel-id

docker compose --project-name ares restart hermes
```

With Android emulator (requires `/dev/kvm`, bare metal only):
```bash
docker compose --project-name ares --profile android up -d
```

---

**Services started (both options):**

| Container | Purpose | Port |
|-----------|---------|------|
| `ares-swarmclaw` | SwarmClaw web UI — multi-engagement dashboard | 3456 |
| `ares-hermes` | Hermes + all MCP servers + HTTP API | 8643 (internal) |
| `ares-mobsf` | MoBSF static analysis (shared — hash-isolated) | 8100 |
| `zap-{engagement-id}` | OWASP ZAP — spawned per engagement, torn down after delivery | dynamic 18000–19000 |
| `ares-android` | Android emulator (optional, `--profile android`, Linux+KVM only) | 5555, 6080 |

The `ares-tools` image is the terminal that Hermes spawns for each session — it has all security tools (nmap, sqlmap, nuclei, ffuf, dalfox, subfinder, Playwright, testssl.sh, wordlists) pre-installed.

---

### Mobile Testing (Android)

ADB runs inside the `ares-hermes` container. USB and network devices are reached via a bridge to the ADB server on the host — no USB passthrough into Docker required.

**Before starting the stack**, make the host ADB server listen on all interfaces:
```bash
adb kill-server && adb -a -P 5037 nodaemon server start
```

Three device scenarios:

| Scenario | Setup | `ADB_SERIAL` in `.env` |
|----------|-------|------------------------|
| **Docker emulator** (Linux + `/dev/kvm`) | `docker compose --profile android up -d` | `localhost:5555` |
| **Android Studio AVD** (macOS / Windows) | Start AVD in Android Studio normally | `localhost:5554` (or check `adb devices`) |
| **USB phone** (connected to host) | Authorize once on the phone | serial from `adb devices` (e.g. `R58N12345`) |
| **Wi-Fi / Tailscale phone** | Enable wireless ADB on phone | `<device-ip>:5555` |

On Linux servers, `host.docker.internal` resolves via the `extra_hosts` mapping in `compose.yml`. If it doesn't, set `ANDROID_ADB_SERVER_HOST=172.17.0.1` in `.env` (the docker0 bridge IP).

The Docker emulator (`ares-android`) requires `/dev/kvm` — bare metal only. VMs need nested virtualization enabled. ADB (`localhost:5555`) and the VNC screen viewer (`http://localhost:6080`) are bound to `127.0.0.1` only.

---

### Option C: Bare-Metal via Claude Code

For production use, full Hindsight memory support, and physical device testing.

**Prerequisites:** Ubuntu Server 22.04+, Claude Code on your workstation.

Open Claude Code and paste:

```
Deploy the Ares pentest stack following CLAUDE.md at https://github.com/munalabs/ares
Target machine: USER@HOST (password: PASSWORD)
```

Claude Code reads `CLAUDE.md` and executes the full install remotely via SSH — tools, containers, Hermes profile, Discord gateway, SwarmClaw — stopping only at human action points (OAuth login, API keys, Discord bot setup).

---

### Run an Engagement

**Via SwarmClaw (web UI):** Open `http://localhost:3456` → new conversation → paste your engagement brief.

**Via Discord:** Create a thread in your pentest forum channel and send:

```
Full web app assessment on https://staging.target.com
Scope: staging.target.com only
Auth: admin@target.com / password123
Test accounts: user1@target.com / pass1, user2@target.com / pass2
Destructive: yes
Go.
```

The agent runs all phases autonomously and delivers findings + report as file attachments (Discord) or downloadable files from the SwarmClaw session.

---

## Key Design Decisions

**Why SwarmClaw for team use?**
Discord is great for personal use — one bot, one channel, mobile notifications. For a pentest team, Discord bots don't scale: one bot token = one WebSocket connection, per-engagement threading is manual, and report delivery is awkward as file attachments. SwarmClaw gives each pentester a proper multi-session dashboard, persistent session archives, and downloadable reports — no bot infrastructure needed.

**Why Hermes?**
Hermes is a multi-model orchestrator with persistent sessions, skill routing, HTTP API server, and MCP server management. Running a 100+ turn pentest session requires all of these — a single Claude conversation context would exhaust.

**Why not ZAP MCP?**
ZAP MCP addon v0.0.1 alpha hardcodes `127.0.0.1` as its bind address and enforces HTTPS. Both `-config` flags to override these are ignored at runtime. Ares uses ZAP via its REST API at `http://{container-ip}:8080/JSON/` instead.

**Why per-engagement output dirs?**
Parallel engagements writing to `/pentest-output/` root overwrite each other's reports and evidence. Each engagement gets `/pentest-output/{target}_{timestamp}/` at Phase 0 before any tools run.

**Why delegate CVSS to Opus?**
In controlled testing, Sonnet consistently downgraded severity by one tier on auth bypass, mass assignment, and JWT storage findings compared to Opus. CVSS scoring is delegated to Opus with explicit calibration guidance to prevent systematic under-rating.

**Why not API key for Anthropic?**
Nico's credits live on his claude.ai account as extra usage credits, accessible via OAuth token (Claude Code login), not a standard API key. Hermes auto-refreshes OAuth tokens from `~/.claude/.credentials.json` — a hardcoded API key would go stale and cause retry storms.

---

## Configuration Reference

`config.yaml` — main profile config. Key settings:

```yaml
model:
  default: claude-sonnet-4-6   # orchestrator model — fast and cheap
  provider: anthropic

smart_model_routing:
  enabled: true
  cheap_model:
    model: claude-haiku-4-5-20251001  # trivial turns auto-routed here

compression:
  threshold: 0.50      # compress when context reaches 50% of window
  protect_last_n: 30   # keep last 30 turns verbatim — never compress active findings

terminal:
  backend: docker                # all tool execution in isolated container
  container_persistent: true     # same container reused across turns (faster)
  docker_volumes:
    - "/home/nico/pentest-output:/pentest-output"  # output survives container restarts
```

The `SKILL.md` for `pentest-orchestrate` is the engagement brain — it contains the full methodology, tool invocation patterns, model routing rules, and output format.

---

## Limitations

- **Authorization required.** This tool is for authorized security testing only. The profile will not proceed without explicit scope confirmation.
- **No WAF bypass.** Ares uses standard tool flags. Evasion against enterprise WAFs (Cloudflare, Akamai) requires manual tuning.
- **MoBSF mobile testing** requires a physical Android device or KVM-enabled host for the emulator (`/dev/kvm`).
- **GitNexus** is [PolyForm Noncommercial](https://polyformproject.org/licenses/noncommercial/1.0.0/) — personal/internal use only.

---

## License

MIT. Component licenses: pentest-ai (MIT), GitNexus (PolyForm Noncommercial), Hermes Agent (Apache 2.0), ZAP (Apache 2.0), MoBSF (GPL-3.0), Frida (wxWindows Library License).
