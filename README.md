# Ares — Autonomous Pentest Agent

Ares is a penetration testing profile for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It turns a bare server or local workstation into a fully autonomous pentest platform — operated via a web UI, Discord, or both simultaneously.

You send a target URL and credentials. The agent runs a full OWASP WSTG assessment, validates every finding with a working proof of concept, and delivers a Markdown/HTML report with PoC scripts. No manual steps during the engagement.

---

## What It Does

- **Full OWASP WSTG coverage** — 13 testing phases, 80+ test cases across recon, auth, authz, injection, business logic, client-side, API, and mobile
- **Validated findings only** — every finding requires a working PoC before it lands in the report. No theoretical vulnerabilities.
- **Per-engagement isolation** — each run creates `/pentest-output/{target}_{timestamp}/` so parallel engagements never conflict
- **Attack chains** — correlates individual findings into exploitable end-to-end scenarios with CVSS 4.0 scoring
- **Detection engineering** — Sigma rules and MITRE ATT&CK Navigator layers for every validated finding
- **Mobile testing** — static analysis (MoBSF), dynamic instrumentation (Frida SSL unpinning, crypto hooks), device control (ADB)
- **White-box support** — clone target repos into `~/ares-workspace/`; the agent reads source at `/workspace/` inside all containers
- **Memory across engagements** — Hindsight extracts findings and patterns after each session, building institutional knowledge over time

---

## Architecture

```
Open WebUI (http://localhost:3000)     Discord Forum Thread
(multi-engagement, model selector)     (one thread = one engagement)
              │                                    │
              └────────────────┬───────────────────┘
                               ▼
         Hermes Gateway — pentest profile
         HTTP API :8643 (OpenAI-compatible) + optional Discord
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     Claude Sonnet 4.6    Haiku 4.5       Claude Opus 4.6
     (orchestrator)     (trivial turns,  (deep analysis —
                         auto-routed)    delegate_task)
                               │
              ┌────────────────┼────────────────────────┐
              ▼                ▼                ▼        ▼
     MCP: playwright   MCP: pentest-ai   MCP: gitnexus  ...
     (headless Chrome) (27 scan/exploit  (GitHub source
                        tools)            analysis)

     MCP: mobsf        MCP: adb          MCP: frida
     (static analysis) (device control)  (dynamic instrumentation)
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

| Component | Purpose | Port |
|-----------|---------|------|
| [Open WebUI](https://github.com/open-webui/open-webui) | Web UI — multi-engagement chat, model selector | 3000 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.9.0+ | Orchestrator, HTTP API, Discord gateway, skill engine | 9119 (dashboard), 8643 (API) |
| [MoBSF](https://github.com/MobSF/Mobile-Security-Framework-MobSF) | Mobile static analysis (APK/IPA/APPX) | 8100 |
| [OWASP ZAP](https://www.zaproxy.org/) | Web scanner, spider, passive scanner | dynamic (per engagement) |
| [pentest-ai](https://github.com/0xSteph/pentest-ai) | MCP server — 27 scan/exploit tools | stdio |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Headless Chromium — DOM testing, auth crawl | stdio |
| MCP: mobsf / adb / frida | Mobile testing MCP servers (vendored in `mcp/`) | stdio |
| `ares-tools` image | Terminal container — all security tools pre-installed | spawned per session |

**Minimum hardware:** 4 cores, 8GB RAM, 50GB disk, macOS (Docker Desktop) or Linux

**Recommended:** 8+ cores, 16GB+ RAM, 200GB SSD (for ZAP + MoBSF + concurrent sessions)

---

## Skills

`skills/pentest-orchestrate/` is the master orchestrator. It runs all 13 phases and delegates to sub-skills:

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
| `pentest-report-merge-and-regenerate` | Consolidated Markdown + HTML report assembly |

---

## Testing Phases

| Phase | Coverage |
|-------|----------|
| 0 | Scope lock, per-engagement ZAP container spawn, output dir setup |
| 1 | Recon — nmap, nuclei, subfinder, ZAP spider + AJAX spider, authenticated Playwright crawl, ffuf content discovery |
| 2 | Passive analysis — ZAP alerts, security headers, JS source analysis, cookie flags, client-side storage |
| 3 | Auth & authz — IDOR across all ID parameters, broken function-level auth, mass assignment, JWT attacks, CSRF |
| 4 | Injection — SQLi (sqlmap), XSS (dalfox), DOM XSS (Playwright), SSRF, SSTI, command injection, XXE, file upload abuse |
| 5 | Business logic — race conditions, rate limiting, workflow bypass, WebSocket auth, CORS |
| 6 | Validation + chain building — Opus confirms every finding, builds attack chains, scores CVSS 4.0 |
| 7 | Report — executive summary, per-finding detail with PoC + evidence, Sigma rules, ATT&CK layer, delivery |
| 8 | Error handling — stack traces, framework debug pages |
| 9 | Cryptography — TLS config (testssl.sh), weak hashing, padding oracle |
| 10 | Client-side — DOM XSS sinks, postMessage, XSSI, clickjacking |
| 11 | API — REST rate limiting, BOLA, GraphQL introspection, WebSocket |
| 12 | Business logic deep dive — multi-step workflow bypass, negative quantities, TOCTOU |
| 13 | Mobile — MoBSF static analysis, Frida dynamic (SSL unpinning, crypto hooks), ADB device testing |

---

## Output

Every engagement writes to an isolated directory inside the named Docker volume:

```
/pentest-output/
  {target-slug}_{YYYYMMDD_HHMMSS}/
    final-report.html           — full technical report, browser-ready
    evidence/
      F-01_sqli_response.txt    — raw request/response for each finding
      phase-2-summary.md        — phase summaries (compaction resilience)
    screenshots/
      F-05_xss.png              — browser evidence for client-side findings
    pocs/
      F-01_sqli.sh              — standalone PoC script per finding
  {target-slug}_{timestamp}-FINAL.tar.gz
```

The report and tarball are delivered as Discord attachments (if Discord is enabled) or accessible via the Open WebUI session.

---

## Quick Start

```bash
git clone https://github.com/munalabs/ares.git
cd ares/docker
./setup.sh
```

`setup.sh` prompts for your Anthropic token, optionally Discord, builds images, starts the stack, and verifies everything. First run takes ~5 minutes.

Open **http://localhost:3000** → select a model → start a new chat → send your engagement brief.

**Prerequisites:** Docker Engine + Docker Compose v2, your user in the `docker` group. No root required.

---

### Adding Discord

Answer **y** to the Discord prompt during `setup.sh`, or add to `.env` after setup:

```bash
# .env
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_ALLOWED_USERS=your-discord-user-id
DISCORD_FREE_RESPONSE_CHANNELS=your-forum-channel-id

docker compose --project-name ares restart hermes
```

Each thread in your pentest forum channel becomes an isolated engagement session.

---

### Run an Engagement

**Via Open WebUI:** Open `http://localhost:3000` → new chat → paste your brief:

```
Full web app assessment on https://staging.target.com
Scope: staging.target.com only
Auth: admin@target.com / password123
Test accounts: user1@target.com / pass1, user2@target.com / pass2
Destructive: yes
Go.
```

**Via Discord:** Create a thread in your pentest forum channel and send the same brief.

---

### White-box / Local File Access

Hermes and all terminal containers it spawns have `/workspace` bind-mounted from `~/ares-workspace/` on the host. Use it for anything the agent needs direct access to:

```bash
# White-box pentest — clone repo locally
cd ~/ares-workspace && git clone https://github.com/your-org/target-app

# → tell Hermes: "review /workspace/target-app/ for auth issues"
```

```bash
# Mobile testing — drop APK locally
cp MyApp.apk ~/ares-workspace/

# → tell Hermes: "analyze /workspace/MyApp.apk with MoBSF"
```

Files placed in `~/ares-workspace/` are immediately visible at `/workspace/` without restarting the stack.

---

### Mobile Testing (Android)

The ADB MCP server runs inside the `ares-hermes` container and connects to the ADB server running on your host machine — no USB passthrough into Docker required.

**macOS (Android Studio AVD):**

```bash
# Install socat (one-time)
brew install socat

# Run once per session (or add to ~/.zshrc for persistence):
nohup socat TCP-LISTEN:5038,reuseaddr,fork TCP:127.0.0.1:5037 \
    >/tmp/ares-socat.log 2>&1 &

# Connect your emulator over TCP so Hermes can reach it:
adb -s emulator-5554 tcpip 5555 && adb connect 127.0.0.1:5555
```

Then run the automated setup:
```bash
cd ares/docker && ./setup.sh --android
```

`setup.sh --android` detects your Docker Desktop VM bridge IP (typically `192.168.64.1`), starts the socat proxy, connects the emulator, and writes `ANDROID_ADB_SERVER_HOST`, `ANDROID_ADB_SERVER_PORT`, and `ADB_SERIAL` to `.env`. Run it any time you need to reconfigure Android without rebuilding the stack.

**Linux (Docker emulator, requires `/dev/kvm`):**

```bash
docker compose --project-name ares --profile android up -d
```

The `ares-android` container (`budtmo/docker-android`) runs Android 13 with ADB on port 5555 and a VNC screen viewer at `http://localhost:6080`.

**Device scenarios:**

| Scenario | `ADB_SERIAL` in `.env` |
|----------|------------------------|
| macOS Android Studio AVD | `127.0.0.1:5555` (after socat + tcpip setup above) |
| Linux Docker emulator | `localhost:5555` |
| USB phone (host) | serial from `adb devices` (e.g. `R58N12345`) |
| Wi-Fi / Tailscale phone | `<device-ip>:5555` |

> **Note:** `host.docker.internal` on macOS Docker Desktop resolves to `172.17.0.1` (the Docker VM bridge), not the Mac. `setup.sh --android` handles this automatically.

---

### Bare-Metal via Claude Code

For production deployments — full Hindsight memory, physical device testing, no Docker Desktop overhead.

Open Claude Code and run:

```
Deploy the Ares pentest stack following CLAUDE.md at https://github.com/munalabs/ares
Target machine: USER@HOST (password: PASSWORD)
```

Claude Code reads `CLAUDE.md` and executes the full install remotely via SSH — tools, containers, Hermes profile, Discord gateway — stopping only at human action points (OAuth login, API keys, Discord bot setup).

---

## Security Tools (ares-tools image)

All tools are pre-installed in the terminal container that Hermes spawns per session:

| Category | Tools |
|----------|-------|
| Web scanning | nmap, nuclei, nikto, whatweb, wafw00f |
| Fuzzing | ffuf, sqlmap |
| XSS / injection | dalfox, commix |
| TLS/SSL | sslyze, testssl.sh |
| Recon | subfinder, httpx |
| HTTP utils | curl, httpx |
| Secrets | gitleaks, jwt_tool |
| Mobile | adb, frida (via MCP) |
| Wordlists | SecLists common.txt, raft-medium-dirs.txt, api-endpoints.txt |

---

## Key Design Decisions

**Why Open WebUI?**
Open WebUI gives a clean multi-conversation UI without requiring a Discord bot. It connects to Hermes's OpenAI-compatible API endpoint (`/v1`), supports multiple simultaneous sessions, and has a model selector for switching between Hermes profiles. No forks, no custom frontend code.

**Why Hermes?**
Hermes is a multi-model orchestrator with persistent sessions, skill routing, HTTP API server, and MCP server management. A 100+ turn pentest session exhausts a single Claude conversation context — Hermes handles compression, model routing, and resumption automatically.

**Why not ZAP MCP?**
ZAP MCP addon v0.0.1 alpha hardcodes `127.0.0.1` as its bind address and enforces HTTPS. Both `-config` flags to override these are ignored at runtime. Ares uses ZAP via its REST API directly instead.

**Why per-engagement ZAP containers?**
A shared ZAP instance accumulates state across engagements — old scan trees, stale alerts, mixed session data. Per-engagement containers give each run a clean ZAP state, then are torn down after report delivery.

**Why delegate CVSS to Opus?**
In controlled testing, Sonnet consistently downgraded severity by one tier on auth bypass, mass assignment, and JWT storage findings compared to Opus. CVSS scoring is delegated to Opus with explicit calibration guidance to prevent systematic under-rating.

**Why socat on macOS instead of `adb -a`?**
On macOS, Android Studio's ADB daemon respawns immediately and re-locks itself to `127.0.0.1`. The `-a` (listen on all interfaces) flag is silently ignored. socat bridges the Docker Desktop VM network to the Mac's localhost ADB, which works regardless of which process owns the ADB server.

---

## Configuration Reference

`docker/config.yaml` — baked into `ares-hermes` at build time. Key settings:

```yaml
model:
  default: claude-sonnet-4-6
  provider: anthropic

smart_model_routing:
  enabled: true
  cheap_model:
    model: claude-haiku-4-5-20251001

compression:
  threshold: 0.50       # compress when context reaches 50% of window
  protect_last_n: 30    # keep last 30 turns verbatim

terminal:
  backend: docker
  docker_image: ares-tools:latest
  container_persistent: true
  docker_volumes:
    - "ares-pentest-output:/pentest-output"
    - "${WORKSPACE_DIR}:/workspace"
    - "/var/run/docker.sock:/var/run/docker.sock"
```

> **Important:** `${VAR:-default}` bash fallback syntax in `config.yaml` is passed as a literal string by Hermes — it does not expand. Use plain `${VAR}` and set defaults in `.env.example`.

---

## Limitations

- **Authorization required.** The profile will not proceed without explicit scope confirmation.
- **No WAF bypass.** Ares uses standard tool flags. Evasion against enterprise WAFs requires manual tuning.
- **Android emulator** (`--profile android`) requires `/dev/kvm` — bare metal only.
- **GitNexus** is [PolyForm Noncommercial](https://polyformproject.org/licenses/noncommercial/1.0.0/) — personal/internal use only.
- **Open WebUI** cannot directly accept binary file uploads (APKs, binaries) — use `~/ares-workspace/` for files Hermes needs to access.

---

## License

MIT. Component licenses: pentest-ai (MIT), GitNexus (PolyForm Noncommercial), Hermes Agent (Apache 2.0), ZAP (Apache 2.0), MoBSF (GPL-3.0), Frida (wxWindows Library License), Open WebUI (MIT).
