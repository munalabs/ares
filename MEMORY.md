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
- MCP servers: playwright (headless Chromium), pentest-ai (27 tools), gitnexus (15 tools)
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
- sqlmap, nuclei, ffuf, dalfox, subfinder, nmap, nikto, commix
- testssl.sh at ~/tools/testssl.sh/testssl.sh
- pentest-ai CLI at ~/tools/pentest-ai/
- Wordlists at ~/wordlists/: common.txt, raft-medium-directories.txt, raft-medium-files.txt, sqli-generic.txt, xss-portswigger.txt
- Nuclei templates at ~/.nuclei-templates/

## Data Persistence
- Findings persist in pentest-ai SQLite DB across sessions
- Engagement records at ~/.hermes/pentest-engagements/

## Methodology
- Follow OWASP WSTG methodology as defined in SOUL.md
- Validate every finding with execution-based PoC
- Never report unconfirmed findings

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
