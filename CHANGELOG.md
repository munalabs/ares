# Changelog

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
