# Changelog

## [Unreleased] — 2026-04-23

### Added

#### apk-sast MCP Server (`mcp/apk_sast.py`)

New MCP server implementing Option C static analysis: deterministic extraction tools, LLM skill does the reasoning. No verdicts in code — only structured evidence.

**7 tools:**
- `decompile_apk` — run apktool + jadx, return decompile_dir and paths
- `parse_manifest` — structured AndroidManifest.xml JSON (components, permissions, SDK versions)
- `build_call_graph` — Smali `invoke-*` → `{callees, callers}` per in-scope class (scope-filtered, capped)
- `grep_smali` — regex over in-scope Smali files with context lines (app package only, library blocklist applied)
- `run_rule_context` — aggregate all evidence for one rule: manifest findings + code matches + call graph context
- `list_rules` — all rules with MASVS v2.0 refs and suggested severity
- `get_masvs` — MASVS v2.0 control lookup by ID

**11 SAST rules** (gaps from DLH comparative analysis):
- `strandhogg` — taskAffinity/launchMode attack, StrandHogg 1.0 + 2.0 (targetSdk < 29)
- `pending_intent_mutable` — PendingIntent without FLAG_IMMUTABLE
- `biometric_without_crypto` — BiometricPrompt/FingerprintManager without CryptoObject binding
- `zip_slip` — ZipEntry.getName() without path sanitization
- `fragment_injection` — exported PreferenceActivity without isValidFragment()
- `unsafe_reflection` — Class.forName / Method.invoke with external input
- `insecure_deserialization` — ObjectInputStream.readObject() on untrusted data
- `jetpack_compose_saveable_leak` — sensitive data in rememberSaveable()
- `exported_no_permission` — exported component without android:permission
- `tapjacking` — activity without filterTouchesWhenObscured
- `intent_scheme_webview` — intent:// scheme in WebView without shouldOverrideUrlLoading() validation

**Architecture:** library blocklist (20+ prefixes) + package whitelist for scope filtering. Call graph built from Smali `invoke-*` instructions, used for call_graph_relevant rules to trace whether attacker-controlled input reaches a dangerous sink. All outputs capped to prevent context pollution.

**MASVS v2.0 embedded:** 18 controls (STORAGE, CRYPTO, AUTH, NETWORK, PLATFORM, CODE, RESILIENCE, PRIVACY) with title and URL. Returned inline in `run_rule_context` output and available standalone via `get_masvs`.

**Tested on InsecureBankv2:**
- `exported_no_permission` → 7 components correctly identified (LoginActivity, PostLogin, DoTransfer, ViewStatement, ChangePassword, MyBroadCastReceiver, TrackUserContentProvider)
- `strandhogg` → 0 findings (correct — InsecureBankv2 has no taskAffinity issues)
- `grep_smali` → correctly scoped to `com.android.insecurebankv2`, SharedPreferences found, no library noise

#### Security Standards Reference (`skills/shared/security-standards.md`)

Unified reference for standard control tagging across all skills.

- **`standard_ref` format** — JSON field and Markdown line format for all findings
- **MASVS v2.0 table** — 21 controls with finding category examples
- **OWASP WSTG table** — 36 test IDs mapped to finding categories (SQLi→WSTG-INPV-05, IDOR→WSTG-ATHZ-04, etc.)
- **OWASP CI/CD Top 10 table** — CICD-SEC-01 through CICD-SEC-10 with typical findings

Skills updated to use `standard_ref`:
- `pentest-orchestrate` — `**Standard:** [WSTG-XX-YY](url)` in finding template + WSTG quick-map table
- `pentest-ci-cd-pipeline` — `**Standard:** [CICD-SEC-XX](url)` in finding template + CICD-SEC quick-map
- `pentest-mobile-static-fallback` — Extended Rule Coverage section with apk-sast orchestration and MASVS-tagged finding format

#### Architecture Documentation (`docs/ARES-OVERVIEW.md`)

New standalone reference document with Mermaid diagrams:
- System architecture (components, interfaces, models, MCP server stack)
- Deployment modes comparison (Docker Compose vs bare-metal)
- Web pentest flow (Phases 0–7 with verification loop)
- Mobile testing flow (static dual-track + device-gated dynamic)
- Finder-Verifier loop sequence diagram with finding states
- Model routing decision tree (Sonnet/Opus/Haiku)
- Engagement isolation diagram
- Standards coverage map
- Report deliverables
- Key operational constraints table

#### Docker: apktool + jadx in ares-hermes (`docker/Dockerfile.hermes`)

apk-sast MCP runs inside the hermes container. Added:
- `default-jre-headless` — JRE required by apktool
- `apktool 2.9.3` — APK decompiler (pinned via ARG, wrapped as `java -jar` shell script)
- `jadx 1.5.0` — optional Java source decompiler (pinned via ARG)
- `unzip` — required for jadx zip extraction

### Fixed

#### `decompile_apk`: removed `--no-res`, added XML validation (`mcp/apk_sast.py`)

`--no-res` caused apktool to leave the AndroidManifest.xml in binary AXML format on certain APKs (reproduced on InsecureBankv2). The binary manifest is not parseable by ElementTree, causing `_detect_package()` to silently return an empty string and all manifest-based rules to produce no findings.

Fix: removed `--no-res` from the decompile invocation. apktool decodes the manifest regardless of this flag — `--no-res` only skips decoding `res/` directory resources. The performance difference is negligible for pentest use.

Additionally, the idempotency check now validates the manifest with `ET.parse()` instead of checking existence only. A previous partial run that left a binary manifest now triggers re-decompilation automatically instead of silently returning bad results.

---

## [Unreleased] — 2026-04-21

### Added

#### Burp Pro MCP Integration

- **`docker/burp-proxy.py`** — stdlib-only Python bridge that translates Hermes's Streamable HTTP MCP transport to Burp's legacy SSE MCP transport.

  Burp's official MCP extension (BApp Store) uses the older SSE protocol: `GET /` delivers a `sessionId` via an event stream; `POST /?sessionId=xxx` carries JSON-RPC requests; responses arrive back over the SSE channel. Hermes's MCP client uses Streamable HTTP (plain `POST /` → JSON response body). The two are wire-incompatible.

  `burp-proxy.py` maintains a persistent SSE connection to Burp (`127.0.0.1:9876`), correlates responses to pending requests via a per-request `threading.Queue` keyed on JSON-RPC `id`, and presents a plain HTTP server to Hermes on `192.168.64.1:9877`. No third-party dependencies.

  Key design choices:
  - `BurpSSEClient` runs in a daemon thread — reconnects automatically when Burp's 60-second SSE timeout fires
  - Per-request `Queue` for response correlation (Burp delivers responses out-of-band on the SSE stream, not in the POST response body)
  - `ThreadingMixIn` on the HTTP server so concurrent Hermes requests don't block each other
  - Python 3.9-compatible (`Optional[T]` instead of `T | None` union syntax)

- **`docker/burp-start.sh`** — operator script to wire up the Burp integration:
  1. Verifies Burp MCP is reachable on `127.0.0.1:9876`
  2. Warns if the proxy listener is not reachable on `${DOCKER_HOST_IP}:8091`
  3. Kills any existing `burp-proxy.py` process and starts a fresh one
  4. Verifies the bridge is reachable from inside the `ares-hermes` container
  5. Restarts `ares-hermes` so it picks up the Burp MCP server on next session start

  Usage: `./burp-start.sh` to enable, `./burp-start.sh --stop` to disable.

- **`docker/config.yaml`** — added `burp` MCP server entry:

  ```yaml
  mcp_servers:
    burp:
      url: "http://${ANDROID_ADB_SERVER_HOST}:9877/"
      headers:
        Host: "127.0.0.1:9876"
      enabled: true
  ```

  `ANDROID_ADB_SERVER_HOST` is already set to `192.168.64.1` (the Docker Desktop VM bridge IP) in `.env`, so the URL expands to `http://192.168.64.1:9877/`. The `Host` header override is required because Burp's MCP extension validates the `Host` header against its bind address.

  On session start, Hermes registers **28 Burp MCP tools**: proxy history retrieval, regex history search, Repeater tab creation, Intruder, active scanner issues, Collaborator payload generation and interaction polling, proxy intercept state control, project/user options read/write, active editor contents, and encoding utilities.

### Fixed

- **`docker/hermes-entrypoint.sh`** — added `--insecure` flag to `hermes dashboard --host 0.0.0.0`.

  Hermes v0.9.0 introduced a security check that raises `SystemExit` when the dashboard is asked to bind to a non-localhost address without explicit opt-in. The background `hermes dashboard &` process was crashing silently on every container start (producing a zombie in the process table and a "Refusing to bind to 0.0.0.0" log line), while the gateway continued running normally. The `--insecure` flag opts in to the public binding, matching the intended behaviour (the dashboard is already auth-protected by `HERMES_API_KEY` and only reachable on the mapped port `9119`).
