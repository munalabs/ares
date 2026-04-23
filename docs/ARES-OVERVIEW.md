# Ares — Architecture & Component Reference

Ares is an autonomous penetration testing platform built on the Hermes agent runtime.
It runs a structured multi-phase assessment — web application, mobile (Android), or CI/CD pipeline —
and produces a consolidated report with working PoC scripts for every finding.

Every finding passes through a live execution loop before it appears in the report.
There are no theoretical vulnerabilities.

---

## System Architecture

```mermaid
graph TD
    subgraph Interfaces
        Discord[Discord Bot]
        WebUI[SwarmClaw Web UI]
        Direct[Direct API]
    end

    subgraph Core["Hermes Runtime"]
        Gateway[Gateway Service]
        Agent[Agent Loop\nmax 200 turns]
        Compress[Context Compression\nHaiku 4.5]
    end

    subgraph Models["Claude API"]
        Sonnet[Sonnet 4.6\nOrchestration]
        Opus[Opus 4.6\nExploitation / CVSS]
        Haiku[Haiku 4.5\nTrivial turns]
    end

    subgraph MCP["MCP Servers"]
        Playwright[Playwright\nbrowser automation]
        PentestAI[pentest-ai\nengagement state]
        ZAP[ZAP\nper-engagement]
        Burp[Burp Pro\nSSE bridge]
        MobSF[MobSF\nstatic analysis]
        ADB[ADB\ndevice control]
        Frida[Frida\ndynamic instrumentation]
        APKSAST[apk-sast\ncall graph + rules]
        GitNexus[gitnexus\ngit/GitHub]
    end

    subgraph Tools["CLI Tools (in terminal container)"]
        nmap & nuclei & ffuf & subfinder
        sqlmap & dalfox & commix & nikto
        testssl["testssl.sh"]
    end

    subgraph Storage
        Output["/pentest-output/\nfindings · PoCs · reports"]
    end

    Discord & WebUI & Direct --> Gateway
    Gateway --> Agent
    Agent <--> Sonnet
    Agent -->|delegate_task| Opus
    Agent -.->|smart routing| Haiku
    Agent --> MCP
    Agent --> Tools
    Compress -.->|compaction| Agent
    MCP --> Storage
    Tools --> Storage
```

---

## Deployment Modes

```mermaid
graph LR
    subgraph Docker["Docker Compose (recommended)"]
        Compose[compose.yml] --> Hermes[ares-hermes container]
        Compose --> MobSFSvc[MoBSF container :8100]
        Hermes -->|spawns per engagement| ZAPCont[ZAP container\ndynamic port]
        Hermes -->|mounts| Vol["/pentest-output volume"]
    end

    subgraph BM["Bare-metal (SSH)"]
        CLAUDE["CLAUDE.md\n19-phase guide"] --> Ubuntu[Ubuntu 24.04]
        Ubuntu --> HermesD[hermes daemon\nsystemd user]
        Ubuntu --> MobSFD[MoBSF :8100]
        Ubuntu --> ZAPD[ZAP :8090]
    end
```

| | Docker Compose | Bare-metal |
|---|---|---|
| Setup time | ~20 min | ~2 hours |
| ZAP | Per-engagement container | Shared instance |
| MCP paths | `/opt/mcp/` | `/home/USER/tools/iris/` |
| Config | `docker/config.yaml` | `config.yaml` |

---

## MCP Server Stack

```mermaid
graph LR
    subgraph Web
        Playwright -->|browser automation| Chrome[Chromium]
        PentestAI -->|engagement state\nSQLite| DB[(SQLite)]
        ZAP -->|active scanner\nspider| Target
        Burp -->|proxy · repeater\n28 tools| BurpPro[Burp Pro]
        GitNexus -->|git · GitHub API| Repos
    end

    subgraph Mobile
        MobSF -->|static APK analysis\nREST API| MobSFSvc[MoBSF service]
        ADB -->|device control\n30 tools| Device[Android Device\nor Emulator]
        Frida -->|dynamic instrumentation\n18 tools| Device
        APKSAST["apk-sast\n7 tools\n11 rules"] -->|apktool · call graph| APK[APK file]
    end

    style APKSAST fill:#4a9,color:#fff
    style Burp fill:#9a4,color:#fff
```

### apk-sast tools

| Tool | Purpose |
|---|---|
| `decompile_apk` | Run apktool + jadx, return paths |
| `parse_manifest` | Structured AndroidManifest.xml JSON |
| `build_call_graph` | Smali `invoke-*` → caller/callee map |
| `grep_smali` | Regex over in-scope Smali files |
| `run_rule_context` | Aggregate all evidence for one rule **(primary)** |
| `list_rules` | All rules with MASVS refs |
| `get_masvs` | MASVS v2.0 control lookup |

---

## Skill Architecture

```mermaid
graph TD
    Orchestrate[pentest-orchestrate\n785 lines · v1.1.0]

    subgraph WebSkills["Web sub-skills"]
        BOLA[pentest-bola-validation]
        Race[pentest-race-condition]
        WS[pentest-websocket]
        GQL[pentest-graphql]
        SSRF[pentest-cloud-ssrf]
        LLM[pentest-llm-platform-attacks]
        CICD[pentest-ci-cd-pipeline]
        Edge[pentest-creative-edge-cases]
    end

    subgraph MobileSkills["Mobile sub-skills"]
        MobMCP[pentest-mobile-mcp-workflow]
        MobStatic[pentest-mobile-static-fallback]
        MobDyn[pentest-mobile-dynamic-frida]
        MobSFAPI[pentest-mobsf-api]
    end

    subgraph ReportingSkills["Enrichment sub-skills"]
        Verify[pentest-finding-verification-loop]
        Detect[pentest-detection-engineering]
        ATT[pentest-framework-enrichment]
        Nav[pentest-attack-navigator]
        Flow[pentest-attack-flow]
    end

    subgraph Shared["Shared references"]
        Standards[security-standards.md\nMAVS · WSTG · CICD]
        EngSetup[engagement-setup.md]
        MobVerif[mobile-verification.md]
        Method[pentest-methodology.md]
        Pitfalls[references/pitfalls.md]
    end

    Orchestrate --> WebSkills
    Orchestrate --> MobileSkills
    Orchestrate --> ReportingSkills
    Orchestrate -.->|reads| Shared
    MobStatic -.->|reads| Standards
    CICD -.->|reads| Standards
```

---

## Web Pentest Flow

```mermaid
flowchart TD
    Start([Engagement start\ntarget URL + scope]) --> P0

    P0["**Phase 0 — Setup**\nEngagement ID · ZAP container\n/pentest-output/ENGAGEMENT_ID/\nauth tokens · scope lock"]

    P0 --> P1["**Phase 1 — Recon**\nnmap · nuclei fingerprint\nZAP spider + AJAX spider\nPlaywright authenticated crawl\nffuf content discovery\nsubfinder subdomain enum"]

    P1 --> P2["**Phase 2 — Passive**\nZAP passive alerts\nSecurity headers audit\nCookie flags · CSP · CORS\nJS dangerous sinks\nClient-side storage"]

    P2 --> P3["**Phase 3 — Auth / Authz**\nIDOR matrix (all ID params × all users)\nPrivilege escalation\nMass assignment\nAuth bypass patterns\nCSRF token validation\n↳ delegate Opus for analysis"]

    P3 --> P4["**Phase 4 — Injection**\nSQLi (sqlmap · UNION · blind)\nXSS (dalfox · DOM via Playwright)\nSSRF · SSTI · XXE · RCE\nCommand injection (commix)\nFile upload bypass"]

    P4 --> P5["**Phase 5 — Business Logic**\nRace conditions\nRate limiting bypass\nWebSocket auth + injection\nGraphQL introspection\nCORS misconfigurations\nCloud SSRF (metadata)\n↳ delegate Opus"]

    P5 --> P6_0["**Phase 6.0 — Collect raw findings**\nfindings-raw.json (append-only)\nno validation yet"]

    P6_0 --> P6_1

    subgraph Loop["Phase 6.1 — Finder-Verifier Loop"]
        P6_1["For each finding\n(max 3 iterations)"] --> Finder["**Finder** (Opus)\nGenerate PoC\nExecute vs target\nParse response"]
        Finder --> Verifier["**Verifier** (Opus)\nLoad evidence\nCheck reproducibility"]
        Verifier --> Decision{Decision}
        Decision -->|PASS| Verified([VERIFIED])
        Decision -->|DOWNGRADE| Downgraded([DOWNGRADED])
        Decision -->|REJECT| Finder
        Decision -->|"3 iterations"| Escalated([ESCALATED])
    end

    P6_1 --> Enrich["**Phase 6.2–6.10 — Enrichment**\nAttack chains · CVSS 4.0\nSigma detection rules\nATT&CK + D3FEND mapping\nEngagement cost tracking"]

    Enrich --> P7["**Phase 7 — Report**\nHTML + PDF\nPoC scripts per finding\nVerification audit trail\nDiscord / MEDIA attach"]

    P7 --> End([Engagement complete\ntar.gz packaged])

    style Verified fill:#2a7,color:#fff
    style Downgraded fill:#a72,color:#fff
    style Escalated fill:#a44,color:#fff
```

---

## Mobile Testing Flow (Android)

```mermaid
flowchart TD
    APK([APK uploaded\nto /pentest-output/]) --> Static

    subgraph Static["Static Analysis — two parallel tracks"]
        MobSFTrack["**MobSF track**\nupload → scan → report\npermissions · trackers\nAPKiD · NIAP\nbinary hardening"]
        AndroTrack["**Androguard + apk-sast track**\n6 passes: hardcoded keys\nSharedPrefs · SQL sinks\ncontent providers · receivers\nweak crypto · IPs"]
        MobSFTrack --- AndroTrack
    end

    AndroTrack --> APKSAST["**apk-sast rules** (11 new)\nStrandHogg · pending intent\nbiometric bypass · zip slip\nfragment injection · tapjacking\nunsafe reflection · deserialization\nJetpack Compose · intent scheme"]

    Static --> DeviceCheck{Device\nconnected?}

    DeviceCheck -->|No| StaticOnly[Static-only report\nMAVS-tagged findings]
    DeviceCheck -->|Yes| ADB

    ADB["**ADB — Device Setup**\nlist_devices · device_info\ninstall_apk · start_app\nsecurity_check\n(root? SELinux? encrypted?)"]

    ADB --> RootCheck{Rooted?}

    RootCheck -->|No| LimitedDyn["Limited dynamic\n(no Frida)\nADB shell · logcat\ncontent providers\nscreenshot"]

    RootCheck -->|Yes| Frida["**Frida — Dynamic Instrumentation**\nsetup_frida_server\nbypass_ssl_pinning\nbypass_root_detection\nhook_crypto (30s)\nintercept_http (30s)\nfind_secrets_in_memory\nenumerate_classes"]

    Frida --> CompTest["**Exported Component Testing**\ndump_content_providers\nquery_content_provider\nADB intent → exported activity\nlogcat monitoring"]

    LimitedDyn --> Report
    CompTest --> Report
    StaticOnly --> Report

    Report["**Report**\nStatic findings + MASVS refs\nDynamic findings + evidence\nFrida output (SSL traffic, keys)\nPoC scripts"]
```

---

## The Finder-Verifier Loop

```mermaid
sequenceDiagram
    participant O as Orchestrator<br/>(Sonnet)
    participant F as Finder<br/>(Opus)
    participant T as Target
    participant V as Verifier<br/>(Opus)
    participant FS as File System

    O->>FS: Load findings-raw.json
    loop For each raw finding (max 3 iterations)
        O->>F: finding claim + endpoint + context
        F->>F: Generate minimal PoC<br/>(curl > Python > Playwright)
        F->>T: Execute PoC
        T-->>F: HTTP response
        F->>FS: Save poc-{id}-attempt-{n}.sh<br/>response-{id}-attempt-{n}.txt
        F->>V: PoC + response + claim
        V->>V: Is response unambiguous?<br/>Is PoC reproducible?
        alt PASS
            V-->>O: VERIFIED — promote to report
        else DOWNGRADE
            V-->>O: DOWNGRADED — lower severity, promote
        else REJECT
            V-->>F: Refine technique (next iteration)
        end
    end
    Note over O,V: After 3 iterations without<br/>PASS/DOWNGRADE → ESCALATED
    O->>FS: Write verified-findings.json<br/>rejected-findings.json<br/>escalated-findings.json
```

**Finding states:**

| State | Condition | Report |
|---|---|---|
| VERIFIED | PoC confirmed, evidence unambiguous | Main report |
| DOWNGRADED | Severity reduced, still exploitable | Main report (lower severity) |
| REJECTED | PoC failed, same technique twice | Excluded (logged) |
| ESCALATED | 3 iterations, no consensus | Separate section + manual review |

---

## Model Routing

```mermaid
flowchart LR
    Input[Agent turn] --> Size{chars < 300\nwords < 50?}

    Size -->|Yes| Haiku[Haiku 4.5\nJSON parsing\nformat ops\nlookups]

    Size -->|No| Type{Turn type}

    Type -->|Orchestration\ntool execution\nreport writing| Sonnet[Sonnet 4.6\ndefault]

    Type -->|"delegate_task()"| Reason{reasoning_effort}

    Reason -->|high / xhigh| Opus[Opus 4.6\nexploitation decisions\nCVSS 4.0 scoring\nattack chains\nbusiness logic]

    Reason -->|medium| Sonnet

    style Haiku fill:#6af,color:#000
    style Sonnet fill:#4a9,color:#fff
    style Opus fill:#94a,color:#fff
```

**Opus is explicitly delegated for:**
- Phase 3–5 exploitation analysis
- CVSS 4.0 scoring (calibrated — Sonnet under-rates auth bypass / mass assignment)
- Attack chain building
- Finder and Verifier agents in the verification loop

**Cost reduction:** ~43% vs full-Opus by routing trivial turns to Haiku.

---

## Engagement Isolation

```mermaid
graph TD
    subgraph Engagement1["Engagement A — target-a.com_20260423"]
        ZAP_A[ZAP container\nport 18043]
        OUT_A[/pentest-output/target-a_20260423/]
        FINDINGS_A[findings tagged\nengagement_id=target-a_20260423]
    end

    subgraph Engagement2["Engagement B — target-b.com_20260423"]
        ZAP_B[ZAP container\nport 18071]
        OUT_B[/pentest-output/target-b_20260423/]
        FINDINGS_B[findings tagged\nengagement_id=target-b_20260423]
    end

    Hermes -->|parallel| ZAP_A & ZAP_B
    ZAP_A --> OUT_A --> FINDINGS_A
    ZAP_B --> OUT_B --> FINDINGS_B

    style Engagement1 fill:#2a7,color:#fff,opacity:0.8
    style Engagement2 fill:#a72,color:#fff,opacity:0.8
```

Each engagement gets:
- Unique `ENGAGEMENT_ID = {target_slug}_{timestamp}`
- Dedicated ZAP container on a random port (18000–19000)
- Isolated output directory
- Findings tagged with `engagement_id` (prevents cross-contamination in the verification loop)
- Checkpoint file for sandbox-reset recovery

---

## Standards Coverage

Every finding across all skills includes a `standard_ref` field.

```mermaid
graph LR
    Web["Web findings"] --> WSTG["OWASP WSTG\n36 test IDs\nWSTG-INPV-05, WSTG-ATHZ-04..."]
    Mobile["Mobile findings"] --> MASVS["MASVS v2.0\n21 controls\nMAVS-PLATFORM-1..."]
    Pipeline["CI/CD findings"] --> CICD["OWASP CI/CD Top 10\nCICD-SEC-01...10"]

    WSTG & MASVS & CICD --> Report["Consolidated report\nstandard_ref per finding"]
```

**Finding format:**
```json
{
  "id": "F-03",
  "title": "Mutable PendingIntent in NotificationHelper",
  "severity": "High",
  "cvss": 7.1,
  "standard_ref": {
    "id": "MASVS-PLATFORM-1",
    "name": "The app uses IPC mechanisms securely",
    "url": "https://mas.owasp.org/MASVS/controls/MASVS-PLATFORM-1/"
  }
}
```

Full reference: [`skills/shared/security-standards.md`](../skills/shared/security-standards.md)

---

## Report Deliverables

```mermaid
graph TD
    Report["Phase 7 — Report assembly"] --> HTML["final-report.html\nbrowser-ready, embedded CSS"]
    Report --> PDF["final-report.pdf\nWeasyPrint"]
    Report --> PoCs["pocs/\nF-01_sqli.sh\nF-02_idor.sh\n...one per verified finding"]
    Report --> Audit["verification-audit-trail.md\nfull loop history per finding"]
    Report --> Tarball["ENGAGEMENT_ID-FINAL.tar.gz"]

    HTML & PDF & PoCs & Audit & Tarball --> Discord["Discord MEDIA attach\nor SwarmClaw download"]
```

Each PoC script is self-contained, matches the exact request/response from the verification loop, and runs without modification against the target.

---

## Key Constraints

| Constraint | Value | Reason |
|---|---|---|
| Output dir | `/pentest-output/` | Bind-mounted persistent volume. Never `/tmp/` |
| ZAP address | `$ZAP_URL` from `/tmp/engagement.env` | Per-engagement dynamic port |
| MobSF | host only, not in terminal | `MOBSF_URL` + `MOBSF_API_KEY` env vars |
| Playwright | `browser_evaluate`, not `browser_execute_script` | execute_script crashes MCP process |
| delegate_task | returns text, never writes files | container isolation — write from main agent |
| Tool output cap | varies (40–100 lines) | context pollution prevention |
| Frida | must run as root on device | `su 0` before frida-server start |
| Opus stream timeout | `HERMES_STREAM_STALE_TIMEOUT=900` | xhigh reasoning silent for 3–5 min |

Full pitfall list: [`skills/pentest-orchestrate/references/pitfalls.md`](../skills/pentest-orchestrate/references/pitfalls.md)
