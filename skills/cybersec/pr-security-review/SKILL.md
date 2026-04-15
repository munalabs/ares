---
name: pr-security-review
description: >
  Security review of GitHub Pull Requests. Fetches diff via gh CLI,
  enriches with GitNexus code graph (callers, execution flows, routes,
  blast radius), performs security analysis with adversarial validation,
  and posts review with inline comments. Triggered by webhook or manual invocation.
tags: [security, github, code-review, pr, gitnexus]
---

# PR Security Review

## When to Use

- Triggered by Hermes cron polling argos `/jobs/pending` endpoint
- Manual: `hermes chat -q "Review PR #42 in owner/repo" -s pr-security-review`
- Discord: message appears in #argos-reviews forum thread (informational — Hermes polls, not webhook-triggered)

## Argos v2 Integration (JWT auth, PostgreSQL, poll-based pickup)

**Important:** Discord webhook @mentions do NOT trigger bot auto-response. Use poll-based pickup.

### Architecture

Argos is a **metrics engine** — sensitive data passes through but never lands in the DB.
- Secrets (webhook_secret, discord_webhook_url): Fernet encrypted at rest (key derived from ARGOS_JWT_SECRET via HKDF)
- Source code / finding content: NEVER stored (goes to GitHub PR reviews + Discord threads)
- Finding metadata: hashed titles/paths only — enough for stats, not for reading findings
- GitHub tokens: NEVER stored (Hermes uses its own gh CLI auth)
- DB leak impact: LOW — public metadata + encrypted blobs

### Auth Model

Two JWT roles (HS256, signed with ARGOS_JWT_SECRET):

| Role | Token TTL | Used by | Can do |
|---|---|---|---|
| `admin` | 1h | Nico (curl/dashboard) | CRUD repos, manage tokens, view stats |
| `service` | 30d (rotatable) | Hermes cron | Poll jobs, claim, complete, post findings |

```bash
# Get admin token
curl -s -X POST $ARGOS_URL/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}' | jq .token

# Create service token for Hermes (admin required)
curl -s -X POST $ARGOS_URL/auth/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hermes-pentest","repos":["*"]}'
```

Service tokens are repo-scoped — a client-specific token can only see that client's jobs.

### API Contract

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | Public | Returns `{"status":"ok"}` only |
| `POST /webhook/github` | GitHub HMAC (per-repo encrypted secrets) | Receives PR events |
| `POST /auth/login` | None (username+password) | → admin JWT (1h) |
| `POST /auth/tokens` | Admin JWT | Create service token (30d) |
| `GET /auth/tokens` | Admin JWT | List service tokens |
| `DELETE /auth/tokens/{id}` | Admin JWT | Revoke token |
| `GET/POST /api/repos` | Admin JWT | CRUD repo config |
| `GET /api/jobs/pending` | Service JWT | Poll for work |
| `POST /api/jobs/{id}/claim` | Service JWT | Atomic claim (UPDATE WHERE status='queued') |
| `POST /api/jobs/{id}/complete` | Service JWT | Metrics + findings → Discord |
| `GET /api/jobs` | Admin JWT | Query job history |
| `GET /api/findings` | Admin JWT | Query finding summaries |
| `GET /api/stats` | Admin JWT | Dashboard metrics |

### Poll → Claim → Review → Complete

```bash
# 1. Poll for pending jobs
curl -s -H "Authorization: Bearer $SERVICE_TOKEN" $ARGOS_URL/api/jobs/pending

# 2. Claim a job (atomic — returns repo, pr, severity_threshold, cost_tag, model_override)
curl -s -X POST -H "Authorization: Bearer $SERVICE_TOKEN" $ARGOS_URL/api/jobs/$JOB_ID/claim

# 3. Execute review (Steps 1-5 below)

# 4. Complete with metrics + findings (argos posts findings to Discord thread)
curl -s -X POST -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "completed",
    "findings": ["discord msg 1", "discord msg 2"],
    "metrics": {"model":"sonnet","tokens_in":40000,"tokens_out":13000,"cost_usd":0.35,"duration_sec":180,"findings_total":8,"findings_count":{"critical":2,"high":3,"medium":3}},
    "finding_summaries": [{"severity":"critical","cwe":"CWE-89","title_hash":"abc123","file_path_hash":"def456","line_number":128,"validated":true}]
  }' \
  $ARGOS_URL/api/jobs/$JOB_ID/complete
```

The `findings` array = pre-formatted Discord messages (≤2000 chars each). Argos posts to correct thread.
The `metrics` dict = stored in jobs.review_metrics JSONB for dashboard/cost tracking.
The `finding_summaries` array = hashed metadata stored in findings_summary table (no content).

### Repo Setup (admin)

```bash
# Register a repo with encrypted webhook secret + Discord config
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"full_name":"munalabs/argos","webhook_secret":"abc123...","discord_webhook_url":"https://discord.com/api/webhooks/...","severity_threshold":"low","cost_tag":"internal"}' \
  $ARGOS_URL/api/repos
```

Argos source: https://github.com/munalabs/argos (v2.0.0+)
Stack: FastAPI + asyncpg + PostgreSQL 16 + PyJWT + Fernet (cryptography) + httpx
Deploy: `docker compose up -d` or bare metal with external PostgreSQL
See `github-app-pr-security-review` skill for full deploy/setup instructions.

## Workflow

### Step 1: Fetch PR Context

```bash
# Get PR metadata
gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json title,body,files,additions,deletions,baseRefName,headRefOid

# Get the diff
gh pr diff <PR_NUMBER> --repo <OWNER/REPO>
```

If the diff is >3000 lines, focus on security-relevant files:
- Files matching: auth, session, login, password, token, crypto, key, secret, api, route, handler, middleware, upload, query, sql, eval, exec, permission, role, admin, payment, checkout

### Step 2: Enrich with GitNexus (if available)

For each changed function/class/method in the diff:

1. **`mcp_gitnexus_context`** — Get callers, callees, execution flows, module membership
2. **`mcp_gitnexus_impact`** — Get blast radius (who breaks if this changes)
3. **`mcp_gitnexus_api_impact`** — If it's a route handler, get consumers + shape mismatches
4. **`mcp_gitnexus_detect_changes`** — Map changed symbols to affected execution flows

This context is CRITICAL for severity assessment:
- Same SQL injection in a helper with 1 caller = MEDIUM
- Same SQL injection in /api/login handler with 30 consumers on the auth flow = CRITICAL

If GitNexus is unavailable, proceed with diff-only analysis.

### Step 3: Security Analysis

Analyze the diff for what it INTRODUCES or MODIFIES. Do NOT report:
- Pre-existing issues the PR doesn't touch
- Style issues, best practices, or non-security concerns
- Theoretical issues with no realistic attack path

**Focus areas by file type:**

| File pattern | Check for |
|---|---|
| `**/auth/**`, `**/login*`, `**/session*` | Auth bypass, session fixation, weak token generation |
| `**/api/**`, `**/route*`, `**/handler*` | IDOR, missing auth middleware, mass assignment, injection |
| `**/db/**`, `**/model*`, `**/query*` | SQL injection, NoSQL injection, ORM bypass |
| `**/upload*`, `**/file*` | Path traversal, unrestricted upload, MIME bypass |
| `**/template*`, `**/view*`, `**/render*` | XSS (reflected, stored, DOM), template injection |
| `**/crypto*`, `**/hash*`, `**/encrypt*` | Weak algorithms, hardcoded keys, IV reuse |
| `**/.env*`, `**/config*`, `**/secret*` | Hardcoded credentials, exposed secrets |
| `**/middleware*`, `**/cors*`, `**/csp*` | Missing security headers, CORS misconfiguration |

**Severity guide:**
- CRITICAL: RCE, auth bypass, SQL injection with data access, SSRF to internal services
- HIGH: Stored XSS, IDOR, privilege escalation, hardcoded secrets, path traversal
- MEDIUM: Reflected XSS, CSRF, information disclosure, missing security headers
- LOW: Verbose errors, minor information leaks, suboptimal crypto

### Step 4: Adversarial Validation (MANDATORY)

For EVERY finding, before reporting it, argue AGAINST it:

1. Are there framework-level protections? (ORM parameterization, template auto-escaping, CSP)
2. Is the input actually user-controlled, or from a trusted source?
3. Does the type system or language runtime prevent the described attack?
4. Are there upstream validators, middleware, or sanitizers?
5. Is this a test file, dead code, or unreachable path?
6. Would the GitNexus context show this is on a non-user-facing path?

**If you cannot disprove it → VALIDATED**
**If you find strong mitigating factors → DROP IT silently**
**If uncertain → mark as UNCONFIRMED**

FALSE POSITIVES ARE WORSE THAN MISSED FINDINGS.

### Step 5: Post Review to GitHub

**If findings exist:**

```bash
# Build review body with inline comments
gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews \
  --method POST \
  -f commit_id="{head_sha}" \
  -f event="COMMENT" \
  -f body="## 🔒 Security Review

Found N issue(s).

*Analyzed by Hermes Security Review*" \
  --jsonArray comments='[
    {
      "path": "app/auth.py",
      "line": 42,
      "body": "### 🔴 CRITICAL: SQL Injection\n\nUser input concatenated into SQL query without parameterization.\n\n**CWE:** CWE-89\n**Fix:** Use parameterized queries:\n```python\ncursor.execute(\"SELECT * FROM users WHERE id = %s\", (user_id,))\n```\n\n<details><summary>🔍 Validation</summary>\n\nNo ORM detected. Input flows directly from request.args to query string. GitNexus confirms this function handles POST /api/users (12 consumers).\n\n</details>"
    }
  ]'
```

**If no findings:**

```bash
gh pr review {PR_NUMBER} --repo {OWNER/REPO} --comment \
  --body "## ✅ Security Review — No Issues Found

No security concerns identified in this PR.

*Analyzed by Hermes Security Review*"
```

**If critical/high findings (request changes):**

```bash
# Use REQUEST_CHANGES event instead of COMMENT
-f event="REQUEST_CHANGES"
```

## Finding Format (for inline comments)

```
### {emoji} {SEVERITY}: {Title}

{Description — what's wrong and why it matters}

**CWE:** CWE-XXX
**OWASP:** AXX:2021 (if applicable)
**Blast radius:** {N callers, on {flow_name} execution flow} (from GitNexus)

**Fix:**
{Specific code suggestion}

<details><summary>🔍 Validation</summary>

{Why this survived adversarial validation — what defenses were checked and why they don't apply}

</details>
```

Severity emojis: 🔴 CRITICAL | 🟠 HIGH | 🟡 MEDIUM | 🔵 LOW

## Pitfalls

1. **gh CLI auth** — Ensure GH_TOKEN or GITHUB_TOKEN env var is set with repo + pull_request write scope
2. **Large PRs** — If >3000 lines, filter to security-relevant files first
3. **Inline comment line numbers** — Must be within the diff hunk range, not absolute file lines. Use the diff `@@` headers to map.
4. **Use JSON file for review posting** — The `gh api` inline `-f 'comments[]'` array syntax breaks with complex nested JSON. Write a JSON file to /tmp and use `--input`:
   ```bash
   # Write review payload to temp file
   echo '{"commit_id":"SHA","event":"COMMENT","body":"summary","comments":[...]}' > /tmp/review.json
   gh api repos/OWNER/REPO/pulls/NUMBER/reviews --method POST --input /tmp/review.json
   ```
5. **Rate limits** — GitHub API has rate limits. Space requests if reviewing multiple PRs.
6. **Draft PRs** — Skip these (webhook handler already filters them)
7. **Repo location** — Argos project source: https://github.com/munalabs/argos (test-app/ has 26+ intentional vulns for testing)
8. **Discord webhook vs bot mention** — Webhook-posted messages with `<@bot_id>` do NOT trigger bot auto-response in Discord. Bots ignore webhook-origin messages to prevent loops. Always use poll-based pickup via argos API.
9. **Thread-per-repo** — Discord forum creates one thread per repo (e.g., "🔒 munalabs/argos"). All PRs from the same repo go to the same thread. Thread IDs are persisted in encrypted discord_config in repos table.
10. **GitHub can't REQUEST_CHANGES on own PR** — If the GH token belongs to the PR author, `event: "REQUEST_CHANGES"` returns 422. Fall back to `event: "COMMENT"`. Use a separate bot/service account token for reviews to get full functionality.
11. **Discord 2000 char limit** — Findings reports exceed the limit. Split messages at logical boundaries (per-finding) and send sequentially. Write payloads to temp JSON files to avoid shell escaping issues with complex markdown.
12. **GitHub webhook must be added BEFORE opening PR** — If the webhook is added after the PR is created, no event fires. Push an empty commit (`git commit --allow-empty -m "trigger"`) to generate a `synchronize` event that the webhook will catch.
13. **Force push may not trigger webhook** — `git push --force` doesn't always fire `synchronize` events reliably. Use a regular commit + push instead.
14. **Discord notification is NOT the trigger** — The Discord message is purely informational for humans. Hermes picks up work by polling `/jobs/pending`. The Discord thread shows: (a) a short "⏳ review queued" notification when PR arrives, (b) the full findings report after Hermes completes analysis.
15. **Findings go to Discord via argos API** — When completing a job, include `findings` array in the POST body to `/api/jobs/{id}/complete`. Also include `metrics` (cost/perf data) and `finding_summaries` (hashed metadata). Argos handles posting findings to the correct Discord thread and storing metrics. Don't post to Discord directly — let argos own the integration.
16a. **Argos stores metrics only** — No source code, no finding descriptions, no PoCs, no GitHub tokens in the DB. Finding titles and file paths are SHA256 hashed. Webhook secrets and Discord URLs are Fernet encrypted. If the DB leaks, attacker gets statistics and encrypted blobs — LOW impact.
16b. **Don't store static API keys** — Use JWT tokens (short-lived for admin, 30d rotatable for service). Static keys like `HERMES_API_KEY` were replaced with JWT auth in v2. Argos's own PR review (F-3) flagged this exact anti-pattern.
16. **Attack chains are high-value** — Always look for finding combinations (e.g., hardcoded creds + RCE endpoint = unauthenticated RCE). These get executive attention and elevate the overall assessment quality.
17. **Always-on GPU can't replace Claude (as of 2026)** — Three blockers: (a) tool use quality gap, (b) 200K vs 32-128K context window, (c) deep reasoning for pentest analysis. Local models work for pattern scanning + triage, not for the agentic backbone. Hybrid: local for scanning, cloud for validation.
18. **Cloudflare Quick Tunnels blocked in some regions** — La Liga (Spain) blocks trycloudflare.com domains. Use ngrok as alternative for dev tunnels: `ngrok http 8080`. But with poll-based architecture, tunnels are only needed during development — in production argos has a public domain and Hermes polls it outbound.
19. **On-demand GPU is 14x cheaper than always-on** — At 160 PRs/mo, ~23h GPU time vs 720h always-on. Use Hetzner GEX44 (RTX 4000 Ada) on-demand or Vast.ai/RunPod for testing. Benchmark local model quality before committing to infra.
