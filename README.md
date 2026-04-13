# Ares — Autonomous Pentest Agent

Ares is a penetration testing profile for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It turns a bare Ubuntu server into a fully autonomous pentest workstation, operated entirely through Discord — one forum thread per engagement.

You send a target URL and credentials. The agent runs a full OWASP WSTG assessment, validates every finding with a working proof of concept, and delivers a Markdown/HTML/PDF report with PoC scripts attached to the thread. No manual steps during the engagement.

---

## What It Does

- **Full OWASP WSTG coverage** — 13 testing phases, 80+ test cases across recon, auth, authz, injection, business logic, client-side, API, and mobile
- **Validated findings only** — every finding requires a working PoC before it lands in the report. No theoretical vulnerabilities.
- **Per-engagement isolation** — each run creates `/pentest-output/{target}_{timestamp}/` so parallel engagements never conflict
- **Attack chains** — correlates individual findings into exploitable end-to-end scenarios with CVSS 4.0 scoring
- **Detection engineering** — Sigma rules and MITRE ATT&CK Navigator layers for every validated finding
- **Mobile testing** — static analysis (MoBSF), dynamic instrumentation (Frida SSL unpinning, crypto hooks), device control (ADB)
- **Memory across engagements** — Hindsight extracts findings and patterns after each session, building institutional knowledge over time
- **Discord-native delivery** — report, PoC scripts, and tarball attached directly to the engagement thread

---

## Architecture

```
Discord Forum Thread
        │  (one thread = one engagement)
        ▼
Hermes Gateway — pentest profile
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
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.8.0+ | Orchestrator, Discord gateway, skill engine | Host (systemd user service) |
| [pentest-ai](https://github.com/0xSteph/pentest-ai) | MCP server — 27 scan/exploit tools | Host (spawned by Hermes) |
| [OWASP ZAP](https://www.zaproxy.org/) | Web scanner, spider, passive scanner | Docker container |
| [MoBSF](https://github.com/MobSF/Mobile-Security-Framework-MobSF) | Mobile static analysis (APK/IPA/APPX) | Docker container |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Headless Chromium — DOM testing, auth crawl | Host (spawned by Hermes) |
| [Iris](https://github.com/munalabs/iris) | MCP servers for MoBSF, ADB, Frida | Host (spawned by Hermes) |
| [Claude Code](https://claude.ai/code) | Anthropic OAuth token source | Host |
| [Hindsight](https://github.com/NousResearch/hindsight) | Persistent memory — cross-session learning | Host (systemd user service) |
| sqlmap, nuclei, ffuf, dalfox | Injection/scan CLI tools | Host (called by pentest-ai) |
| nmap, nikto, subfinder, httpx | Recon CLI tools | Host (called by pentest-ai) |
| testssl.sh | TLS/SSL analysis | Host |
| Discord bot | Operator interface | Connects to Discord gateway |

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

### Prerequisites

- Ubuntu Server 22.04+ (bare metal or VM)
- An Anthropic account with credits (OAuth — not API key)
- A Google AI Studio API key (compression + delegation — much cheaper than Opus)
- A Groq API key (free tier — for Hindsight memory extraction)
- A Discord server where you control bot permissions
- Claude Code installed on the machine running Claude (your workstation)

### Deploy

Open a conversation with Claude Code and paste:

```
Deploy the Ares pentest stack following CLAUDE.md at https://github.com/munalabs/ares
Target machine: USER@HOST (password: PASSWORD)
```

Claude Code reads the `CLAUDE.md` deployment guide and executes the full install remotely via SSH. It handles all 18 phases — tools, Docker containers, Hermes profile, Discord gateway — stopping only at the 6 human action points (reboot, OAuth login, API keys, Discord bot setup).

Total time: ~30-45 minutes of automated work + ~15 minutes of human actions.

### Run an Engagement

Create a thread in your pentest forum channel and send:

```
Full web app assessment on https://staging.target.com
Scope: staging.target.com only
Auth: admin@target.com / password123
Test accounts: user1@target.com / pass1, user2@target.com / pass2
Destructive: yes
Go.
```

The agent runs all phases autonomously and delivers findings + report as Discord file attachments.

---

## Key Design Decisions

**Why Hermes?**
Hermes is a multi-model orchestrator with native Discord integration, persistent sessions, skill routing, and MCP server management. Running a 100+ turn pentest session requires all of these — a single Claude conversation context would exhaust.

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
