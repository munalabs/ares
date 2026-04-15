---
name: github-app-pr-security-review
description: >
  Deploy and operate Argos v2 — a PR security review service backed by PostgreSQL,
  JWT auth, and Fernet-encrypted secrets. Metrics-only storage (no code/findings at rest).
  Hermes polls for work via service JWT. Source: github.com/your-org/argos
version: 4.0.0
metadata:
  hermes:
    tags: [github-app, security-review, webhook, fastapi, hermes-dispatch, PR, argos]
    related_skills: [pr-security-review, github-code-review, webhook-subscriptions]
---

# Argos v2 — PR Security Review Service

## When to Use

- Deploy automated security review on GitHub PRs
- Building security-review-as-a-service for clients
- Need multi-tenant PR security with per-repo isolation

Source: https://github.com/your-org/argos

## Architecture

```
GitHub PR → argos webhook (HMAC, per-repo encrypted secrets)
         → job queued in PostgreSQL → Discord notification (informational)
                                    ↓
Hermes cron (every 5 min) → GET /api/jobs/pending (service JWT)
                          → POST /api/jobs/{id}/claim (atomic)
                          → fetch diff, analyze, post GitHub review
                          → POST /api/jobs/{id}/complete (metrics + findings → Discord)
```

**Key principle:** Argos is a METRICS ENGINE, not a data store. Source code, finding details, and PoCs never touch the database. The DB contains only metadata, statistics, and encrypted config blobs.

## Data Security Model

Three tiers of secret handling:

| Tier | What | How stored |
|---|---|---|
| ENV VARS (never in DB) | JWT secret, admin password hash | Process environment only |
| ENCRYPTED AT REST | Webhook secrets, Discord URLs | Fernet (key derived from JWT secret via HKDF-SHA256) |
| NEVER STORED | GitHub tokens, source code, finding content, PoCs | Passes through memory only |

**If the DB leaks, attacker gets:** repo names (public), PR numbers (public), finding counts (statistics), encrypted blobs (useless without env key), bcrypt hash (slow to crack). **Impact: LOW.**

## Deploy

### Docker (recommended)

```bash
git clone https://github.com/your-org/argos.git && cd argos
cp .env.example .env

# Generate JWT secret
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Set ARGOS_JWT_SECRET and ARGOS_ADMIN_PASSWORD in .env

docker compose up -d
curl http://localhost:8080/health  # → {"status":"ok"}
```

### Bare metal

```bash
pip install -r requirements.txt
# Needs PostgreSQL running (set DATABASE_URL in env)
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Initial Setup Flow

```bash
# 1. Login → admin JWT (1h TTL)
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' | jq -r .token)

# 2. Register a repo (webhook secret + Discord URL encrypted at rest)
curl -s -X POST http://localhost:8080/api/repos \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "your-org/argos",
    "webhook_secret": "your-hmac-secret",
    "discord_webhook_url": "https://discord.com/api/webhooks/...",
    "severity_threshold": "low",
    "cost_tag": "internal"
  }'

# 3. Create service token for Hermes (30d TTL, rotatable)
HERMES_TOKEN=$(curl -s -X POST http://localhost:8080/auth/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hermes-pentest","repos":["*"]}' | jq -r .token)
# Store HERMES_TOKEN securely — shown only once

# 4. Add GitHub webhook on repo
# https://github.com/OWNER/REPO/settings/hooks/new
# Payload URL: https://argos.yourdomain.com/webhook/github
# Content type: application/json
# Secret: same as webhook_secret above
# Events: Pull requests only
```

## API Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | Public | `{"status":"ok"}` |
| `POST /webhook/github` | HMAC | GitHub PR events |
| `POST /auth/login` | None | `{username, password}` → admin JWT (1h) |
| `POST /auth/tokens` | Admin JWT | Create service token (30d) |
| `GET /auth/tokens` | Admin JWT | List service tokens |
| `DELETE /auth/tokens/{id}` | Admin JWT | Revoke token |
| `GET /api/repos` | Admin JWT | List repos |
| `POST /api/repos` | Admin JWT | Register repo (secrets encrypted) |
| `GET /api/repos/{owner}/{repo}` | Admin JWT | Get repo config |
| `PATCH /api/repos/{owner}/{repo}` | Admin JWT | Update repo config |
| `DELETE /api/repos/{owner}/{repo}` | Admin JWT | Remove repo |
| `GET /api/jobs/pending` | Service JWT | Poll for work (repo-scoped) |
| `POST /api/jobs/{id}/claim` | Service JWT | Atomic claim |
| `POST /api/jobs/{id}/complete` | Service JWT | Metrics + findings → Discord |
| `GET /api/jobs/{id}` | Service JWT | Job status |
| `GET /api/jobs` | Admin JWT | Query job history |
| `GET /api/findings` | Admin JWT | Finding summaries (hashed metadata) |
| `GET /api/stats` | Admin JWT | Aggregated dashboard metrics |

## Hermes Cron Setup

```python
cronjob(
    action="create",
    schedule="*/5 * * * *",
    name="argos-pr-review",
    prompt="Poll https://argos.yourdomain.com/api/jobs/pending with Authorization: Bearer <SERVICE_TOKEN>. For each pending job: claim it, fetch the PR diff with `gh pr diff {pr} --repo {repo}`, analyze for security vulnerabilities, post inline review comments via `gh api`, then POST /api/jobs/{id}/complete with metrics and findings.",
    skills=["pr-security-review"]
)
```

## Multi-Tenant Isolation

Each repo gets its own encrypted config. Service tokens can be repo-scoped.

```bash
# Client-specific token (can only see client's repos)
curl -s -X POST http://localhost:8080/auth/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"client-acme","repos":["client-org/their-app"]}'
```

| Isolation layer | Mechanism |
|---|---|
| GitHub tokens | Hermes has its own, never stored in argos |
| Webhook secrets | Per-repo, Fernet encrypted |
| Discord channels | Per-repo encrypted config (webhook_url + thread_id) |
| Severity threshold | Per-repo setting |
| Cost tracking | Per-repo `cost_tag` |
| Token scope | Service tokens scoped to specific repos |

## Database Schema

```sql
repos           -- full_name, webhook_secret_enc (BYTEA), discord_config_enc (BYTEA),
                -- severity_threshold, cost_tag, model_override, enabled
jobs            -- id (UUID), repo_full_name, pr_number, head_sha, status,
                -- review_metrics (JSONB: model, tokens, cost, duration, finding_counts)
findings_summary -- job_id, severity, cwe, title_hash (SHA256), file_path_hash (SHA256),
                -- validated, developer_action (fixed/dismissed/disputed)
service_tokens  -- name, token_hash (SHA256, for revocation), role, repos_scope,
                -- expires_at, revoked, last_used_at
admin_users     -- username, password_hash (bcrypt)
```

**No plaintext secrets. No source code. No finding content. Hashed metadata only.**

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ARGOS_JWT_SECRET` | ✅ | JWT signing + Fernet key derivation |
| `ARGOS_ADMIN_PASSWORD` | ✅ | Admin password (created on first boot) |
| `POSTGRES_PASSWORD` | — | DB password (default: `argos`) |
| `DATABASE_URL` | — | Full DB URI (docker-compose sets automatically) |
| `GITHUB_WEBHOOK_SECRET` | — | Global fallback HMAC secret |
| `DISCORD_WEBHOOK_URL` | — | Global fallback Discord webhook |
| `SERVICE_TOKEN_TTL_DAYS` | — | Service token TTL (default: 30) |
| `ADMIN_TOKEN_TTL_HOURS` | — | Admin token TTL (default: 1) |
| `ARGOS_ADMIN_USER` | — | Admin username (default: `admin`) |

## Pitfalls & Lessons Learned

1. **Discord @mentions from webhooks DON'T trigger bot auto-response.** Tested and confirmed broken. Use poll-based pickup (Hermes cron → `/api/jobs/pending`). Discord notification is informational only.

2. **Cloudflare Quick Tunnels blocked in Spain** (La Liga DNS blocking). Use ngrok for dev tunnels. In production, argos has a public domain — Hermes polls outbound, no tunnel needed.

3. **GitHub can't REQUEST_CHANGES on own PR.** If the GH token belongs to the PR author, use `event: "COMMENT"` instead. Better: use a separate bot/service account token.

4. **Don't store static API keys.** We flagged this exact anti-pattern (CWE-798) in our own PR review (F-3). Use JWT tokens with expiration and revocation.

5. **Thread-per-repo in Discord forum.** All PRs from the same repo go to one thread. Thread IDs are persisted in encrypted `discord_config_enc`. First PR creates the thread; subsequent PRs post as new messages.

6. **Force push may not trigger webhook.** Use `git commit --allow-empty` + regular push to reliably generate `synchronize` events.

7. **On-demand GPU is 14x cheaper than always-on** at <500 PRs/mo. Don't pay for an always-on GPU unless it serves multiple workloads. Benchmark local model quality before committing to infrastructure.

8. **Infrastructure is not a blocker for MVP.** Ship with cloud APIs first (which you already pay for). GPU provisioning, metrics dashboards, and React UIs are optimizations for after the product works.

9. **Discord 2000 char limit.** Split findings reports at logical boundaries. Write payloads to temp JSON files to avoid shell escaping issues with markdown.

10. **The moat is the system, not the model.** (AISLE research) Scaffold, skills, and adversarial validation matter more than which LLM runs. This is why argos dispatches to Hermes (the system), not a custom LLM wrapper.

## Testing

```bash
cd argos
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -v  # 25 tests: crypto, JWT, schemas, webhook HMAC, config
```

For integration testing: `docker compose up -d` then run the Initial Setup Flow above.
