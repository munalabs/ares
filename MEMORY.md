## CRITICAL OUTPUT RULE
ALL files (reports, PoC scripts, zips, tarballs) MUST be written to /pentest-output/ inside the terminal.
/pentest-output/ is a bind mount: same path works both inside the container AND on the host (symlinked at /pentest-output on host).

**Always reference files using /pentest-output/ paths — the gateway reads them directly from there.**
  CORRECT:   /pentest-output/filename.md
  WRONG:     /tmp/filename.md  ← /tmp is NOT mounted, gateway cannot find it
  WRONG:     ~/pentest-output/filename.md  ← does not exist inside the container
  WRONG:     /root/pentest-output/filename.md  ← does not exist anywhere

**NEVER copy files to /tmp before sending. Send directly from /pentest-output/.**

Example correct final message (use REAL filenames, not placeholders):
  Report saved. [ATTACH:/pentest-output/report-example-com.md]

## Profile
- This is the pentest profile. All testing requires explicit written authorization.

## MCP Tools
- MCP servers: playwright, pentest-ai (29 tools), gitnexus (20 tools), mobsf (18 tools), adb (30 tools), frida (18 tools), apk-sast (7 tools) — 147 tools total
- **CRITICAL: Playwright MCP runs on the HOST machine, NOT inside the Docker terminal container.**
  The terminal container does NOT have Chromium or its dependencies installed.
  NEVER try to install, launch, or use Playwright/Chromium inside the terminal.
  All browser automation MUST go through mcp_playwright_* tools.
- ZAP is NOT available as MCP (v0.0.1 alpha broken). Use ZAP REST API via $ZAP_URL:
  - $ZAP_URL is set in /tmp/engagement.env by Phase 0 (always source it first)
  - Docker Compose: per-engagement ZAP on a dynamic port — $ZAP_URL = http://172.17.0.1:{port}/JSON
  - Bare-metal fallback: shared ZAP — $ZAP_URL = http://$ZAP_CONTAINER_IP:8080/JSON
  - API key is in $ZAP_API_KEY env var (forwarded into terminal)
  - Example: curl "$ZAP_URL/core/view/version/?apikey=$ZAP_API_KEY"
  - NEVER hardcode ZAP address — always use $ZAP_URL from engagement.env

## CLI Tools
**CRITICAL: ALL CLI tools live inside the ares-tools terminal container, NOT in the agent's own process.**
**ALWAYS use `terminal()` to run them. Never run `which nmap` or any tool check directly — it will show "not found" because the agent process has none of these tools.**

Available tools (confirmed in `/usr/local/bin/` and `/usr/bin/` inside the terminal):
- **Scanning:** nmap, nuclei, nikto, whatweb, sslyze
- **Fuzzing:** ffuf, gobuster, sqlmap, commix
- **Injection:** dalfox
- **Recon:** subfinder, httpx, gitleaks, amass
- **Auth/TLS:** jwt_tool, testssl (at `/usr/local/bin/testssl`)
- **Wordlists:** `/wordlists/common.txt`, `/wordlists/raft-medium-dirs.txt`, `/wordlists/api-endpoints.txt`
- **Nuclei templates:** `~/.nuclei-templates/` (pre-downloaded at image build time)

## Data Persistence
- Findings persist in pentest-ai SQLite DB across sessions
- Engagement records at ~/.hermes/pentest-engagements/

## Methodology
- Follow OWASP WSTG methodology as defined in SOUL.md
- Validate every finding with execution-based PoC
- Never report unconfirmed findings

## pentest-ai — External Tools DO NOT WORK in Docker deployment
pentest-ai's `run_tool()` checks tool availability using `shutil.which()` inside its own process,
which runs in the hermes container. The hermes container has NO external security tools.
Tools like nmap, nuclei, sqlmap, ffuf, dalfox, nikto, httpx are in the ares-tools terminal container only.

**NEVER call `mcp_pentest_ai_run_tool` for nmap, nuclei, sqlmap, ffuf, dalfox, nikto, httpx, subfinder, or any CLI tool.**
They will always return "tool not installed" — not because they're missing, but because pentest-ai
checks the wrong container.

**Use pentest-ai ONLY for:**
- Built-in scanners: `scan_ports_builtin`, `scan_headers_builtin`, `scan_ssl_builtin`, `scan_paths_builtin`, `scan_dns_builtin`, `scan_secrets_builtin`
- Engagement management: `start_engagement`, `get_findings`, `get_attack_chains`, `generate_report`, `close_engagement`

**Use `terminal()` for ALL external tool invocations** (nmap, nuclei, sqlmap, ffuf, dalfox, etc.)

## pentest-ai MCP Tool Signatures (CRITICAL — do not hallucinate argument names)
Use EXACTLY these argument names when calling pentest-ai tools:

- start_engagement(target, scope="full", rules_of_engment="", intensity="normal")
  NOTE: argument is "target" NOT "target_url". No "auth" argument exists.
  ALSO: pentest-ai LLM features (auto_chain, auto_validate_pocs) will NOT work in the MCP subprocess.
  The subprocess has no ANTHROPIC_API_KEY (OAuth tokens incompatible with standard SDK x-api-key auth).
  Use pentest-ai for scan tools and findings DB only. Do chaining and validation yourself.
- get_engagement_status(engagement_id)
- get_findings(engagement_id=None, severity=None, status=None)
- get_attack_chains(engagement_id)
- run_recon(target, depth="standard")
- test_web_app(target, engagement_id, auth_credentials=None, focus_areas=None)
- validate_finding(finding_id)
- generate_report(engagement_id)
- scan_ports_builtin(target)
- scan_headers_builtin(target)
- scan_paths_builtin(target)
- scan_dns_builtin(target)
- scan_ssl_builtin(target, port=443)
- scan_secrets_builtin(target)
- builtin_scan(target, scan_type)
- close_engagement(engagement_id)

## apk-sast MCP — Android Static Analysis
- **apk-sast runs in ares-hermes, NOT the terminal container.** apktool and jadx are installed in the hermes image.
- APK must be at a path reachable inside ares-hermes — `/pentest-output/` is always correct. `/workspace/` also works.
- **Primary workflow:** `decompile_apk(apk_path)` first, then `run_rule_context(decompile_dir, rule)` per rule.
- `run_rule_context` returns raw evidence only — you reason about it and decide `is_vulnerable`, `severity`, `confidence`.
- Available rules: `strandhogg`, `pending_intent_mutable`, `biometric_without_crypto`, `zip_slip`, `fragment_injection`, `unsafe_reflection`, `insecure_deserialization`, `jetpack_compose_saveable_leak`, `exported_no_permission`, `tapjacking`, `intent_scheme_webview`
- MASVS v2.0 reference embedded — `get_masvs(id)` returns title + URL for any MASVS control.
- **inject_frida_gadget(decompile_dir, arch, gadget_config_mode)** — patches APK for dynamic analysis without root. Call AFTER `decompile_apk`. Returns `{signed_apk, install_cmd, connect_cmd}`. arch: `arm64-v8a` (physical) | `x86_64` (emulators). Idempotent — safe to call multiple times.
- **sign_apk(apk_path)** — signs any APK with the debug keystore (V1+V2+V3). Use after manual modifications.
- **Pitfall: PENTEST_OUTPUT path.** On macOS Docker Desktop the env var may expand to the host path (e.g. `/Users/nico/ares-pentest-output`). This works because Docker Desktop mounts `/Users` into containers. On Linux Docker it would NOT work — always pass `/pentest-output/` explicitly as `apk_path` or `output_dir`.
- **Pitfall: binary manifest.** If a previous decompile left a corrupted manifest, re-running `decompile_apk` now detects this via XML parse validation and re-decompiles automatically.
- **Pitfall: decompile is cached.** `decompile_apk` skips apktool if the manifest is already valid XML. This means modifications to the decompile dir (e.g. from `inject_frida_gadget`) persist across calls. To force a fresh decompile, delete the sast directory: `rm -rf /pentest-output/sast_{APK_STEM}/`.
- **Frida Gadget connect flow** (after install): `adb forward tcp:27042 tcp:27042 && frida -H 127.0.0.1:27042 -n Gadget`. App pauses on startup (`on_load: wait`) — attach before the OS kills it (within ~30s).

## Mobile App Testing (MobSF)
- **CRITICAL: MobSF runs on the HOST, NOT inside the terminal container. NEVER pip install or start MobSF locally.**
- A MobSF instance is already running and reachable from the terminal via env vars: MOBSF_URL and MOBSF_API_KEY.
- Verify with: `curl -s "$MOBSF_URL/api/v1/scans?page=1" -H "Authorization: $MOBSF_API_KEY"`
- MobSF REST API: $MOBSF_URL/api/v1/ (env var already forwarded into terminal)
- API key in MOBSF_API_KEY env var
- Upload APK/IPA: POST /api/v1/upload (multipart, field name: file)
- Scan: POST /api/v1/scan (scan_type: apk|ipa|appx, file_name, hash)
- JSON report: POST /api/v1/report_json (hash=HASH) — use this for Markdown generation
- Scorecard: POST /api/v1/scorecard (hash=HASH)
- Delete scan: POST /api/v1/delete_scan (hash=HASH)
- User attaches APK/IPA to Discord thread → agent downloads attachment → uploads to MobSF → scans → generates Markdown report → saves to /pentest-output/
- Report output: mobile-report-{appname}.md and mobile-{appname}.tar.gz (written to /pentest-output/)

## Playwright MCP — Known Crashes
- **NEVER use `browser_execute_script`** — it crashes the MCP process with "Connection closed", killing ALL subsequent Playwright calls for the rest of the session. The gateway must be restarted to recover.
- Use `browser_evaluate` for JavaScript execution instead — it runs JS in the page context without crashing the MCP process.
- If Playwright calls start returning empty errors after a `browser_execute_script` attempt, the MCP is dead — tell the user the session needs a restart.

## Terminal Container Env Vars
- docker_forward_env vars (MOBSF_API_KEY, MOBSF_URL, ZAP_API_KEY, ZAP_CONTAINER_IP) are injected at container CREATION time only.
- If a tool says env vars are missing, the persistent container is stale. Fix: run `docker ps -a --format '{{.Names}}' | grep '^hermes-' | xargs docker rm -f` on the server. The next terminal use creates a fresh container with the correct env.

## CRITICAL: delegate_task CANNOT write to /pentest-output

delegate_task spawns a sub-agent container WITHOUT the /pentest-output volume mounted.
Files written by a delegate to /pentest-output/ land in a throwaway sandbox and are LOST.

RULES:
- NEVER use delegate_task to write reports, PoC files, zips, or any deliverable
- delegate_task is for computation/analysis only — have it RETURN results as text
- The MAIN agent writes all files to /pentest-output/ after receiving delegate output

WRONG: delegate_task asking it to write to disk
RIGHT: delegate_task returns text -> you write the file to /pentest-output/ yourself
