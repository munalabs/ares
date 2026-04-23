#!/usr/bin/env python3
"""
Burp MCP transport bridge — stdlib only, no dependencies.

Hermes speaks Streamable HTTP (POST /), Burp speaks SSE (GET / + POST /?sessionId=xxx).
This proxy bridges them:
  Hermes POST → proxy → Burp POST /?sessionId=xxx
  Burp SSE response → proxy → JSON response to Hermes
"""
import argparse
import json
import http.client
import re
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from queue import Queue
from typing import Optional


class BurpSSEClient:
    """Maintains a persistent SSE connection to Burp MCP, correlates responses by JSON-RPC id."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.session_id: Optional[str] = None
        self._pending: dict = {}   # id → Queue
        self._lock = threading.Lock()
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="burp-sse").start()

    def _run(self):
        while True:
            try:
                conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
                conn.request("GET", "/", headers={
                    "Host": f"{self.host}:{self.port}",
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                })
                resp = conn.getresponse()
                if resp.status != 200:
                    print(f"[sse] GET / returned {resp.status}, retrying...", flush=True)
                    time.sleep(3)
                    continue

                print(f"[sse] connected to Burp MCP at {self.host}:{self.port}", flush=True)
                event_type: str | None = None

                while True:
                    raw = resp.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8").rstrip("\r\n")

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if event_type == "endpoint":
                            m = re.search(r"sessionId=([^&\s]+)", data)
                            if m:
                                with self._lock:
                                    self.session_id = m.group(1)
                                self._ready.set()
                                print(f"[sse] session: {self.session_id}", flush=True)
                        elif event_type == "message":
                            try:
                                msg = json.loads(data)
                                rid = msg.get("id")
                                with self._lock:
                                    q = self._pending.get(rid)
                                if q:
                                    q.put(msg)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        event_type = None
                    # blank line resets event_type (already handled above)

            except Exception as exc:
                print(f"[sse] connection lost: {exc}, reconnecting in 3s...", flush=True)
            finally:
                self._ready.clear()
                with self._lock:
                    self.session_id = None
                    for q in self._pending.values():
                        q.put(None)   # unblock waiters
                    self._pending.clear()
            time.sleep(3)

    def send(self, body: bytes) -> Optional[bytes]:
        """Forward one JSON-RPC message to Burp, return response bytes (or None for notifications)."""
        if not self._ready.wait(timeout=15):
            raise RuntimeError("Burp SSE not ready (no sessionId)")

        with self._lock:
            sid = self.session_id

        try:
            msg = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"bad JSON from Hermes: {exc}") from exc

        rid = msg.get("id")       # notifications have no id
        q: Optional[Queue] = None
        if rid is not None:
            q = Queue()
            with self._lock:
                self._pending[rid] = q

        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
            conn.request(
                "POST", f"/?sessionId={sid}",
                body=body,
                headers={
                    "Host": f"{self.host}:{self.port}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            resp = conn.getresponse()
            burp_status = resp.status
            resp.read()
            conn.close()
            print(f"[burp] POST /?sessionId={sid[:8]}... → {burp_status}", flush=True)
            if burp_status >= 400:
                raise RuntimeError(f"Burp rejected POST with {burp_status}")
        except RuntimeError:
            raise
        except Exception as exc:
            with self._lock:
                self._pending.pop(rid, None)
            raise RuntimeError(f"Burp POST failed: {exc}") from exc

        if q is None:
            return None   # notification, no response expected

        result = q.get(timeout=60)
        if result is None:
            raise RuntimeError("SSE connection dropped while waiting for response")
        return json.dumps(result).encode()


_burp: Optional[BurpSSEClient] = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        print(f"[http] GET {self.path} Accept={self.headers.get('Accept','')}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        print(f"[http] POST {self.path} len={length} body={body[:120]}", flush=True)
        try:
            result = _burp.send(body)
            if result:
                print(f"[http] → 200 len={len(result)}", flush=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
            else:
                print(f"[http] → 202 (notification)", flush=True)
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
        except Exception as exc:
            print(f"[http] ERROR → 502: {exc}", flush=True)
            msg = json.dumps({"error": str(exc)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def log_message(self, fmt, *args):
        pass


def main():
    global _burp
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind",     default="192.168.64.1")
    parser.add_argument("--port",     type=int, default=9877)
    parser.add_argument("--upstream", default="127.0.0.1:9876")
    args = parser.parse_args()

    upstream_host, upstream_port = args.upstream.rsplit(":", 1)
    upstream_port = int(upstream_port)

    _burp = BurpSSEClient(upstream_host, upstream_port)

    server = HTTPServer((args.bind, args.port), Handler)
    print(f"Burp MCP bridge: {args.bind}:{args.port} → {args.upstream} (SSE transport)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
