# Security Standards Reference

Tag every finding with the applicable standard control.
This document covers MASVS v2.0 (mobile), OWASP WSTG (web), and OWASP CI/CD Top 10 (pipeline).

## Finding Format

**JSON findings** — include `standard_ref`:
```json
{
  "standard_ref": {
    "id": "WSTG-ATHZ-04",
    "name": "Testing for Insecure Direct Object References",
    "url": "https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/05-Authorization_Testing/04-Testing_for_Insecure_Direct_Object_References"
  }
}
```

**Markdown report line** — include after CVSS:
```
**Standard:** [WSTG-ATHZ-04](url) — Testing for Insecure Direct Object References
```

---

## MASVS v2.0 — Mobile (Android / iOS)

Use for any APK/IPA finding. `apk_sast` MCP returns this automatically per rule.

| Control | Title | Typical finding categories |
|---|---|---|
| MASVS-STORAGE-1 | The app securely stores sensitive data | SharedPrefs plaintext, SQLite unencrypted, files world-readable, rememberSaveable leak |
| MASVS-STORAGE-2 | The app prevents leakage via backups | allowBackup=true + sensitive data |
| MASVS-CRYPTO-1 | Strong cryptography correctly applied | ECB mode, static IV, MD5/SHA1 for passwords, hardcoded keys |
| MASVS-CRYPTO-2 | Key management per industry standards | Keys in SharedPrefs, keys in code, weak KDF |
| MASVS-AUTH-1 | Secure authentication and authorization protocols | No server-side auth, local-only auth check |
| MASVS-AUTH-2 | Server-side auth for sensitive functions | Client-side role check, bypassable auth flow |
| MASVS-AUTH-3 | Sensitive ops require additional authentication | Biometric without CryptoObject, 2FA bypass |
| MASVS-NETWORK-1 | Network traffic secured | Cleartext HTTP, insecure TrustManager, usesCleartextTraffic=true |
| MASVS-NETWORK-2 | Remote endpoint identity verified | SSL pinning absent, invalid cert accepted |
| MASVS-PLATFORM-1 | IPC mechanisms used securely | Exported components without permission, StrandHogg, mutable PendingIntent, tapjacking, fragment injection, intent spoofing |
| MASVS-PLATFORM-2 | WebViews used securely | JS + file access enabled, intent:// scheme, JS interface exposed to web content |
| MASVS-PLATFORM-3 | Appropriate APIs, no deprecated ones | FingerprintManager (deprecated), deprecated crypto APIs |
| MASVS-CODE-1 | Up-to-date platform version | minSdk too low for security guarantees |
| MASVS-CODE-2 | No known vulnerable components | Third-party lib CVEs |
| MASVS-CODE-4 | Validates and sanitizes untrusted input | SQL injection in local DB, zip slip, unsafe reflection, insecure deserialization |
| MASVS-RESILIENCE-1 | Validates platform integrity | No root detection |
| MASVS-RESILIENCE-2 | Anti-tampering mechanisms | debuggable=true, no signature verification |
| MASVS-RESILIENCE-3 | Anti-debugging mechanisms | No anti-debug |
| MASVS-RESILIENCE-4 | Anti-static analysis techniques | No obfuscation on sensitive code |
| MASVS-PRIVACY-1 | Minimizes access to sensitive data | Excessive permissions, unnecessary data collection |
| MASVS-PRIVACY-2 | Prevents unauthorized access to sensitive data | Data exposure to other apps |

URL pattern: `https://mas.owasp.org/MASVS/controls/{ID}/`

---

## OWASP WSTG — Web Application

Use for all web pentest findings. Pick the most specific ID.

| WSTG ID | Name | Maps to |
|---|---|---|
| WSTG-INFO-02 | Fingerprint web server | Tech stack / version disclosure |
| WSTG-INFO-06 | Identify application entry points | Attack surface map |
| WSTG-CONF-01 | Network/infrastructure configuration | Security headers missing, server misconfiguration |
| WSTG-CONF-05 | Enumerate admin interfaces | Admin panel exposed without auth |
| WSTG-CONF-07 | HTTP Strict Transport Security | HSTS absent or short max-age |
| WSTG-CONF-10 | HTTP dangerous methods | PUT / DELETE / TRACE enabled |
| WSTG-ATHN-01 | Credentials over encrypted channel | HTTP login, plaintext password |
| WSTG-ATHN-03 | Weak lockout mechanism | No brute-force protection |
| WSTG-ATHN-06 | Browser cache weakness | Sensitive data in cache |
| WSTG-ATHN-10 | Weaker authentication in alternative channel | OAuth / SSO bypass |
| WSTG-ATHZ-01 | Directory traversal / file inclusion | Path traversal, LFI, RFI |
| WSTG-ATHZ-02 | Bypassing authorization schema | Privilege escalation, RBAC bypass |
| WSTG-ATHZ-03 | Privilege escalation | Vertical IDOR (user → admin) |
| WSTG-ATHZ-04 | Insecure Direct Object References (BOLA/IDOR) | Horizontal IDOR |
| WSTG-SESS-01 | Session management schema | Weak / predictable session tokens |
| WSTG-SESS-02 | Cookie attributes | Missing Secure / HttpOnly / SameSite |
| WSTG-SESS-05 | CSRF | CSRF token absent or bypassable |
| WSTG-SESS-06 | Logout functionality | Tokens not invalidated server-side on logout |
| WSTG-SESS-08 | Session puzzling | Session fixation, cross-role token reuse |
| WSTG-INPV-01 | Reflected XSS | Reflected cross-site scripting |
| WSTG-INPV-02 | Stored XSS | Persistent cross-site scripting |
| WSTG-INPV-05 | SQL injection | SQLi (UNION, error, blind, time) |
| WSTG-INPV-07 | XML injection | XXE (file read, SSRF, blind) |
| WSTG-INPV-08 | SSI injection | Server-side template / include injection |
| WSTG-INPV-11 | Code injection | RCE, SSTI leading to code execution |
| WSTG-INPV-12 | Command injection | OS command injection |
| WSTG-INPV-17 | Host header injection | Host header SSRF, cache poisoning |
| WSTG-INPV-19 | Server-side request forgery | SSRF (cloud metadata, internal services) |
| WSTG-BUSL-01 | Business logic data validation | Negative prices, mass assignment, type confusion |
| WSTG-BUSL-04 | Process timing | Race conditions (TOCTOU) |
| WSTG-BUSL-05 | Function usage limits | Rate limiting bypass, OTP brute force |
| WSTG-BUSL-08 | Upload of unexpected file types | File upload bypass, web shell upload |
| WSTG-CLNT-01 | DOM-based XSS | DOM XSS via JS sinks |
| WSTG-CLNT-07 | Cross-origin resource sharing | CORS misconfiguration with credentials |
| WSTG-CLNT-09 | WebSockets | WebSocket auth bypass, injection |
| WSTG-CLNT-10 | Web messaging | postMessage without origin check |

URL base: `https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/`

---

## OWASP CI/CD Security Top 10

Use for pipeline security findings.

| ID | Name | Typical finding |
|---|---|---|
| CICD-SEC-01 | Insufficient Flow Control Mechanisms | No required reviews, no branch protection, direct push to main |
| CICD-SEC-02 | Inadequate Identity and Access Management | Overly permissive bot tokens, shared accounts, no RBAC on CI |
| CICD-SEC-03 | Dependency Chain Abuse | Unpinned actions (uses: action@main), typosquatted packages, no lockfile |
| CICD-SEC-04 | Poisoned Pipeline Execution (PPE) | User-controlled input in `run:` steps, pull_request_target + checkout |
| CICD-SEC-05 | Insufficient Pipeline-Based Access Controls | Secrets available to all jobs, GITHUB_TOKEN write-all default |
| CICD-SEC-06 | Insufficient Credential Hygiene | Secrets printed in logs, long-lived tokens, env vars leaked |
| CICD-SEC-07 | Insecure System Configuration | Self-hosted runner on shared infra, debug endpoints in prod |
| CICD-SEC-08 | Ungoverned 3rd-Party Services | Unvetted external actions, webhooks without HMAC validation |
| CICD-SEC-09 | Improper Artifact Integrity Validation | Artifacts not signed, no SLSA provenance, hash not verified |
| CICD-SEC-10 | Insufficient Logging and Visibility | No audit log, secrets not masked, no alerting on anomalous jobs |

Reference: `https://owasp.org/www-project-top-10-ci-cd-security-risks/`
