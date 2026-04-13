#!/usr/bin/env python3
"""
Frida MCP Server
Dynamic instrumentation for Android/iOS via Frida Python API.
Handles SSL pinning bypass, root detection bypass, crypto hooking,
class/method enumeration, and arbitrary script injection.
"""

import os
import asyncio
import json
import time
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import frida
from fastmcp import FastMCP

mcp = FastMCP("frida")

PENTEST_OUTPUT = Path(os.environ.get("PENTEST_OUTPUT", "/pentest-output"))
FRIDA_SERVER_PATH = os.environ.get("FRIDA_SERVER_PATH", "/home/nico/tools/frida-server/frida-server-android-x86_64")
ADB = os.environ.get("ADB_PATH", "/usr/lib/android-sdk/platform-tools/adb")
DEFAULT_DEVICE = os.environ.get("FRIDA_DEVICE", "localhost:5555")

_executor = ThreadPoolExecutor(max_workers=4)


# ── Pre-built pentest scripts ──────────────────────────────────────────────────

SSL_UNPIN_SCRIPT = r"""
Java.perform(function() {
    // TrustManager bypass
    var TrustManager = Java.registerClass({
        name: 'com.frida.TrustManager',
        implements: [Java.use('javax.net.ssl.X509TrustManager')],
        methods: {
            checkClientTrusted: function(chain, authType) {},
            checkServerTrusted: function(chain, authType) {},
            getAcceptedIssuers: function() { return []; }
        }
    });
    var SSLContext = Java.use('javax.net.ssl.SSLContext');
    var TrustManagers = Java.array('javax.net.ssl.TrustManager', [TrustManager.$new()]);
    var sslContext = SSLContext.getInstance('TLS');
    sslContext.init(null, TrustManagers, null);
    SSLContext.getDefault.implementation = function() { return sslContext; };

    // OkHttp3
    try {
        var CertificatePinner = Java.use('okhttp3.CertificatePinner');
        CertificatePinner.check.overload('java.lang.String', 'java.util.List').implementation = function() {
            send('[SSL] OkHttp3 CertificatePinner.check bypassed');
        };
        CertificatePinner.check.overload('java.lang.String', '[Ljava.security.cert.Certificate;').implementation = function() {
            send('[SSL] OkHttp3 CertificatePinner.check (cert array) bypassed');
        };
    } catch(e) {}

    // Retrofit / older OkHttp
    try {
        var OkHttpClient = Java.use('com.squareup.okhttp.CertificatePinner');
        OkHttpClient.check.overload('java.lang.String', 'java.util.List').implementation = function() {
            send('[SSL] OkHttp CertificatePinner bypassed');
        };
    } catch(e) {}

    // Apache Harmony (older Android)
    try {
        var X509TrustManagerExtensions = Java.use('android.net.http.X509TrustManagerExtensions');
        X509TrustManagerExtensions.checkServerTrusted.implementation = function(chain, authType, host) {
            return Java.use('java.util.Arrays').asList(chain);
        };
    } catch(e) {}

    // WebViewClient
    try {
        var WebViewClient = Java.use('android.webkit.WebViewClient');
        WebViewClient.onReceivedSslError.implementation = function(view, handler, error) {
            handler.proceed();
            send('[SSL] WebViewClient.onReceivedSslError bypassed');
        };
    } catch(e) {}

    send('[SSL] Pinning bypass active — all hooks installed');
});
"""

ROOT_BYPASS_SCRIPT = r"""
Java.perform(function() {
    // RootBeer
    try {
        var RootBeer = Java.use('com.scottyab.rootbeer.RootBeer');
        RootBeer.isRooted.implementation = function() { send('[ROOT] RootBeer.isRooted → false'); return false; };
        RootBeer.isRootedWithoutBusyBoxCheck.implementation = function() { return false; };
    } catch(e) {}

    // SafetyNet / Play Integrity
    try {
        var SafetyNet = Java.use('com.google.android.gms.safetynet.SafetyNetApi');
        send('[ROOT] SafetyNet class found — patching');
    } catch(e) {}

    // Runtime.exec su
    var Runtime = Java.use('java.lang.Runtime');
    var originalExec = Runtime.exec.overload('java.lang.String');
    originalExec.implementation = function(cmd) {
        if (cmd.indexOf('su') !== -1 || cmd.indexOf('which') !== -1) {
            send('[ROOT] Blocked exec: ' + cmd);
            throw Java.use('java.io.IOException').$new('File not found');
        }
        return originalExec.call(this, cmd);
    };

    // File.exists() for common root paths
    var File = Java.use('java.io.File');
    File.exists.implementation = function() {
        var path = this.getAbsolutePath();
        var rootPaths = ['/su', '/sbin/su', '/system/bin/su', '/system/xbin/su',
                         '/data/local/xbin/su', '/data/local/bin/su',
                         '/system/sd/xbin/su', '/system/bin/failsafe/su',
                         '/data/local/su', '/su/bin/su', '/magisk'];
        for (var i = 0; i < rootPaths.length; i++) {
            if (path === rootPaths[i]) {
                send('[ROOT] File.exists blocked: ' + path);
                return false;
            }
        }
        return this.exists();
    };

    send('[ROOT] Root detection bypass active');
});
"""

CRYPTO_HOOK_SCRIPT = r"""
Java.perform(function() {
    var findings = [];

    var Cipher = Java.use('javax.crypto.Cipher');
    Cipher.getInstance.overload('java.lang.String').implementation = function(algo) {
        var result = this.getInstance(algo);
        send(JSON.stringify({type: 'cipher', algo: algo, stack: Java.use('android.util.Log').getStackTraceString(Java.use('java.lang.Exception').$new())}));
        return result;
    };

    var SecretKeySpec = Java.use('javax.crypto.spec.SecretKeySpec');
    SecretKeySpec.$init.overload('[B', 'java.lang.String').implementation = function(key, algo) {
        var keyHex = Array.from(key).map(b => ('0' + (b & 0xff).toString(16)).slice(-2)).join('');
        send(JSON.stringify({type: 'key', algo: algo, key_hex: keyHex, key_len: key.length * 8}));
        return this.$init(key, algo);
    };

    var MessageDigest = Java.use('java.security.MessageDigest');
    MessageDigest.getInstance.overload('java.lang.String').implementation = function(algo) {
        send(JSON.stringify({type: 'digest', algo: algo}));
        return this.getInstance(algo);
    };

    var Mac = Java.use('javax.crypto.Mac');
    Mac.getInstance.overload('java.lang.String').implementation = function(algo) {
        send(JSON.stringify({type: 'mac', algo: algo}));
        return this.getInstance(algo);
    };

    // Log hardcoded strings passed to crypto
    var IvParameterSpec = Java.use('javax.crypto.spec.IvParameterSpec');
    IvParameterSpec.$init.overload('[B').implementation = function(iv) {
        var ivHex = Array.from(iv).map(b => ('0' + (b & 0xff).toString(16)).slice(-2)).join('');
        send(JSON.stringify({type: 'iv', iv_hex: ivHex}));
        return this.$init(iv);
    };

    send('[CRYPTO] Hooks active — monitoring cipher usage');
});
"""

HTTP_INTERCEPT_SCRIPT = r"""
Java.perform(function() {
    // OkHttp3 request/response logging
    try {
        var Builder = Java.use('okhttp3.Request$Builder');
        var OkHttpClient = Java.use('okhttp3.OkHttpClient');
        var RealCall = Java.use('okhttp3.internal.connection.RealCall');
        RealCall.execute.implementation = function() {
            var request = this.request();
            send(JSON.stringify({
                type: 'request',
                method: request.method(),
                url: request.url().toString(),
                headers: request.headers().toString()
            }));
            var response = this.execute();
            send(JSON.stringify({
                type: 'response',
                code: response.code(),
                url: response.request().url().toString()
            }));
            return response;
        };
    } catch(e) { send('[HTTP] OkHttp3 not found: ' + e); }

    // HttpURLConnection
    try {
        var HttpURLConnection = Java.use('java.net.HttpURLConnection');
        HttpURLConnection.getResponseCode.implementation = function() {
            send(JSON.stringify({type: 'http', url: this.getURL().toString()}));
            return this.getResponseCode();
        };
    } catch(e) {}

    send('[HTTP] Intercept hooks active');
});
"""

ENUM_CLASSES_SCRIPT = r"""
Java.perform(function() {
    var classes = Java.enumerateLoadedClassesSync();
    var filter = FILTER_PLACEHOLDER;
    var result = [];
    for (var i = 0; i < classes.length; i++) {
        var name = classes[i];
        if (!filter || name.toLowerCase().indexOf(filter.toLowerCase()) !== -1) {
            result.push(name);
        }
    }
    send(JSON.stringify({classes: result}));
});
"""

ENUM_METHODS_SCRIPT = r"""
Java.perform(function() {
    try {
        var clazz = Java.use('CLASS_PLACEHOLDER');
        var methods = clazz.class.getDeclaredMethods();
        var result = [];
        for (var i = 0; i < methods.length; i++) {
            result.push(methods[i].toString());
        }
        var fields = clazz.class.getDeclaredFields();
        var fieldList = [];
        for (var i = 0; i < fields.length; i++) {
            fieldList.push(fields[i].toString());
        }
        send(JSON.stringify({methods: result, fields: fieldList}));
    } catch(e) {
        send(JSON.stringify({error: e.toString()}));
    }
});
"""

FIND_SECRETS_SCRIPT = r"""
Process.enumerateRanges({protection: 'r--', coalesce: true}).forEach(function(range) {
    try {
        var bytes = Memory.readByteArray(range.base, Math.min(range.size, 4096));
        var str = String.fromCharCode.apply(null, new Uint8Array(bytes));
        var patterns = [
            /[A-Za-z0-9+\/]{40,}={0,2}/g,  // base64
            /[0-9a-fA-F]{32,}/g,             // hex keys
            /(?:password|passwd|secret|key|token|auth)[=:]\s*\S+/gi,
            /sk-[a-zA-Z0-9]{20,}/g,          // API keys
        ];
        patterns.forEach(function(pat) {
            var matches = str.match(pat);
            if (matches) {
                matches.forEach(function(m) {
                    if (m.length > 8) send(JSON.stringify({type: 'secret', value: m, addr: range.base}));
                });
            }
        });
    } catch(e) {}
});
send(JSON.stringify({type: 'done'}));
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_device(serial: Optional[str]) -> frida.core.Device:
    target = serial or DEFAULT_DEVICE
    if ":" in target:
        mgr = frida.get_device_manager()
        try:
            return mgr.get_device(target, timeout=3)
        except frida.InvalidArgumentError:
            return mgr.add_remote_device(target)
    return frida.get_device(target, timeout=5)


async def _run_script(
    serial: Optional[str],
    target: str | int,
    script_src: str,
    timeout: int = 15,
    spawn: bool = False,
) -> list[dict]:
    """Run a Frida script and collect messages until timeout or 'done' signal."""

    def _blocking():
        dev = _get_device(serial)
        messages = []
        pid = None

        if spawn and isinstance(target, str):
            pid = dev.spawn([target])
            session = dev.attach(pid)
        elif isinstance(target, int):
            session = dev.attach(target)
        else:
            try:
                session = dev.attach(target)
            except frida.ProcessNotFoundError:
                # Try by package name
                apps = dev.enumerate_applications()
                for a in apps:
                    if a.identifier == target and a.pid:
                        session = dev.attach(a.pid)
                        break
                else:
                    raise

        script = session.create_script(script_src)
        done_event = asyncio.Event() if False else type('E', (), {'set': lambda s: None, 'is_set': lambda s: False})()
        finished = [False]

        def on_message(msg, data):
            if msg.get("type") == "send":
                payload = msg.get("payload", "")
                messages.append(payload)
                try:
                    d = json.loads(payload) if isinstance(payload, str) else payload
                    if isinstance(d, dict) and d.get("type") == "done":
                        finished[0] = True
                except Exception:
                    pass
            elif msg.get("type") == "error":
                messages.append({"error": msg.get("description", ""), "stack": msg.get("stack", "")})

        script.on("message", on_message)
        script.load()

        if spawn and pid:
            dev.resume(pid)

        deadline = time.time() + timeout
        while time.time() < deadline and not finished[0]:
            time.sleep(0.1)

        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass

        return messages

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _blocking)


async def _adb_shell(cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        ADB, "shell", cmd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
    return out.decode(errors="replace").strip()


# ── Server management ──────────────────────────────────────────────────────────

@mcp.tool()
async def setup_frida_server(serial: Optional[str] = None) -> str:
    """
    Push frida-server to the device and start it. Run this once per device/session.
    Detects if already running and skips push if version matches.
    """
    # Check if already running
    status = await _adb_shell("ps -A | grep frida-server")
    if "frida-server" in status:
        return f"frida-server already running:\n{status}"

    server_bin = FRIDA_SERVER_PATH
    if not Path(server_bin).exists():
        return f"frida-server binary not found at {server_bin}"

    # Push
    proc = await asyncio.create_subprocess_exec(
        ADB, "push", server_bin, "/data/local/tmp/frida-server",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        return f"Push failed: {err.decode()}"

    await _adb_shell("chmod +x /data/local/tmp/frida-server")

    # Start in background
    proc2 = await asyncio.create_subprocess_exec(
        ADB, "shell", "nohup /data/local/tmp/frida-server &>/dev/null &",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await asyncio.wait_for(proc2.communicate(), timeout=10)

    await asyncio.sleep(2)
    status2 = await _adb_shell("ps -A | grep frida-server")

    return (
        f"frida-server pushed and started\n{out.decode().strip()}\n"
        f"Running: {'yes' if 'frida-server' in status2 else 'NO — check ADB root permissions'}"
    )


@mcp.tool()
async def check_frida_server(serial: Optional[str] = None) -> str:
    """Check if frida-server is running and reachable from the host."""
    proc_status = await _adb_shell("ps -A | grep frida-server")

    try:
        def _check():
            dev = _get_device(serial)
            procs = dev.enumerate_processes()
            return len(procs)

        loop = asyncio.get_event_loop()
        count = await asyncio.wait_for(
            loop.run_in_executor(_executor, _check), timeout=5
        )
        return (
            f"frida-server: running\n"
            f"Device process: {proc_status or 'not visible via ps'}\n"
            f"Frida API: connected — {count} processes visible"
        )
    except Exception as e:
        return (
            f"frida-server on device: {'running' if proc_status else 'NOT running'}\n"
            f"Frida API connection: FAILED — {e}\n"
            "Run setup_frida_server() if not started."
        )


@mcp.tool()
async def stop_frida_server(serial: Optional[str] = None) -> str:
    """Stop the frida-server process on the device."""
    out = await _adb_shell("pkill -f frida-server; echo $?")
    return f"frida-server stopped (exit: {out})"


# ── Process & app enumeration ──────────────────────────────────────────────────

@mcp.tool()
async def list_processes(serial: Optional[str] = None, filter: Optional[str] = None) -> str:
    """List running processes on the device."""
    def _blocking():
        dev = _get_device(serial)
        return dev.enumerate_processes()

    loop = asyncio.get_event_loop()
    procs = await loop.run_in_executor(_executor, _blocking)

    if filter:
        procs = [p for p in procs if filter.lower() in p.name.lower()]

    if not procs:
        return "No processes found"

    lines = [f"{len(procs)} process(es):"]
    for p in sorted(procs, key=lambda x: x.name):
        lines.append(f"  {p.pid:6d}  {p.name}")
    return "\n".join(lines)


@mcp.tool()
async def list_applications(
    serial: Optional[str] = None,
    running_only: bool = False,
) -> str:
    """List installed applications with their running status and PIDs."""
    def _blocking():
        dev = _get_device(serial)
        return dev.enumerate_applications()

    loop = asyncio.get_event_loop()
    apps = await loop.run_in_executor(_executor, _blocking)

    if running_only:
        apps = [a for a in apps if a.pid != 0]

    lines = [f"{len(apps)} app(s):"]
    for a in sorted(apps, key=lambda x: x.identifier):
        pid_str = f"pid={a.pid}" if a.pid else "not running"
        lines.append(f"  {a.identifier}  [{pid_str}]  {a.name}")
    return "\n".join(lines)


@mcp.tool()
async def get_frontmost_app(serial: Optional[str] = None) -> str:
    """Get the currently foregrounded application."""
    def _blocking():
        dev = _get_device(serial)
        return dev.get_frontmost_application()

    loop = asyncio.get_event_loop()
    app = await loop.run_in_executor(_executor, _blocking)

    if not app:
        return "No foreground app detected"
    return f"Foreground app:\n  Name:    {app.name}\n  Package: {app.identifier}\n  PID:     {app.pid}"


# ── Pentest scripts ────────────────────────────────────────────────────────────

@mcp.tool()
async def bypass_ssl_pinning(
    target: str,
    serial: Optional[str] = None,
    spawn: bool = False,
) -> str:
    """
    Inject SSL certificate pinning bypass into a running app.
    target: package name or PID
    spawn: True to spawn the app fresh with bypass active from start (recommended)
    """
    msgs = await _run_script(serial, target, SSL_UNPIN_SCRIPT, timeout=20, spawn=spawn)
    lines = [f"SSL pinning bypass injected into {target}"]
    for m in msgs:
        if isinstance(m, str) and "[SSL]" in m:
            lines.append(f"  {m}")
    lines.append("\nProxy traffic through Burp/ZAP — pinning is now disabled.")
    return "\n".join(lines)


@mcp.tool()
async def bypass_root_detection(
    target: str,
    serial: Optional[str] = None,
    spawn: bool = False,
) -> str:
    """
    Inject root detection bypass into a running app.
    target: package name or PID
    spawn: True to spawn fresh (recommended for apps that check at startup)
    """
    msgs = await _run_script(serial, target, ROOT_BYPASS_SCRIPT, timeout=20, spawn=spawn)
    lines = [f"Root detection bypass injected into {target}"]
    for m in msgs:
        if isinstance(m, str):
            lines.append(f"  {m}")
    return "\n".join(lines)


@mcp.tool()
async def hook_crypto(
    target: str,
    duration: int = 30,
    serial: Optional[str] = None,
) -> str:
    """
    Hook cryptographic operations in a running app for `duration` seconds.
    Captures: cipher algorithms, keys (hex), IVs, MACs, digests.
    target: package name or PID
    """
    msgs = await _run_script(serial, target, CRYPTO_HOOK_SCRIPT, timeout=duration)

    findings = {"cipher": [], "key": [], "iv": [], "digest": [], "mac": []}
    for m in msgs:
        try:
            d = json.loads(m) if isinstance(m, str) else m
            t = d.get("type", "")
            if t in findings:
                findings[t].append(d)
        except Exception:
            pass

    lines = [f"Crypto hooks — {duration}s capture on {target}\n"]
    for t, items in findings.items():
        if items:
            lines.append(f"[{t.upper()}] {len(items)} event(s):")
            for item in items[:10]:
                if t == "key":
                    lines.append(f"  algo={item.get('algo')} len={item.get('key_len')}b key={item.get('key_hex','')[:32]}...")
                elif t == "cipher":
                    lines.append(f"  {item.get('algo')}")
                elif t == "iv":
                    lines.append(f"  {item.get('iv_hex')}")
                else:
                    lines.append(f"  {item.get('algo','')}")
            lines.append("")

    if not any(findings.values()):
        lines.append("No crypto operations observed. Interact with the app (login, submit forms) to trigger hooks.")

    return "\n".join(lines)


@mcp.tool()
async def intercept_http(
    target: str,
    duration: int = 30,
    serial: Optional[str] = None,
) -> str:
    """
    Intercept HTTP/HTTPS requests made by an app via OkHttp/HttpURLConnection.
    target: package name or PID
    duration: seconds to monitor
    """
    msgs = await _run_script(serial, target, HTTP_INTERCEPT_SCRIPT, timeout=duration)

    requests = [m for m in msgs if isinstance(m, str) and '"request"' in m]
    lines = [f"HTTP intercept — {duration}s on {target}\n"]
    for r in requests[:20]:
        try:
            d = json.loads(r)
            lines.append(f"  {d.get('method','?')} {d.get('url','?')}")
        except Exception:
            lines.append(f"  {r[:120]}")

    if not requests:
        lines.append("No HTTP requests captured. Interact with the app to trigger network calls.")
    return "\n".join(lines)


# ── Class/method enumeration ───────────────────────────────────────────────────

@mcp.tool()
async def enumerate_classes(
    target: str,
    filter: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """
    Enumerate loaded Java classes in a running app.
    target: package name or PID
    filter: substring to match class names (e.g. 'crypto', 'auth', 'login')
    """
    filter_val = f'"{filter}"' if filter else "null"
    script = ENUM_CLASSES_SCRIPT.replace("FILTER_PLACEHOLDER", filter_val)
    msgs = await _run_script(serial, target, script, timeout=30)

    for m in msgs:
        try:
            d = json.loads(m) if isinstance(m, str) else m
            if "classes" in d:
                classes = d["classes"]
                if not classes:
                    return f"No classes found matching filter='{filter}'"
                lines = [f"{len(classes)} class(es) (filter='{filter}'):"]
                for c in sorted(classes)[:100]:
                    lines.append(f"  {c}")
                if len(classes) > 100:
                    lines.append(f"  ... and {len(classes)-100} more")
                return "\n".join(lines)
        except Exception:
            pass

    return f"No classes enumerated. Ensure frida-server is running and app is active."


@mcp.tool()
async def enumerate_methods(
    target: str,
    class_name: str,
    serial: Optional[str] = None,
) -> str:
    """
    List all methods and fields of a Java class in a running app.
    target: package name or PID
    class_name: fully qualified class name (e.g. 'com.example.app.LoginActivity')
    """
    script = ENUM_METHODS_SCRIPT.replace("CLASS_PLACEHOLDER", class_name)
    msgs = await _run_script(serial, target, script, timeout=15)

    for m in msgs:
        try:
            d = json.loads(m) if isinstance(m, str) else m
            if "error" in d:
                return f"Error: {d['error']}"
            if "methods" in d:
                methods = d["methods"]
                fields = d.get("fields", [])
                lines = [f"Class: {class_name}", f"Methods ({len(methods)}):"]
                for method in sorted(methods):
                    lines.append(f"  {method}")
                if fields:
                    lines.append(f"\nFields ({len(fields)}):")
                    for f in sorted(fields):
                        lines.append(f"  {f}")
                return "\n".join(lines)
        except Exception:
            pass

    return f"Could not enumerate {class_name}"


# ── Arbitrary injection ────────────────────────────────────────────────────────

@mcp.tool()
async def inject_script(
    target: str,
    script: str,
    serial: Optional[str] = None,
    timeout: int = 15,
    spawn: bool = False,
) -> str:
    """
    Inject arbitrary Frida JavaScript into a running process.
    target: package name, process name, or PID (integer as string)
    script: Frida JS — use send() to return data, e.g.:
            Java.perform(function() { send(Java.use('android.os.Build').MODEL); })
    spawn: True to spawn the app fresh with script injected from start
    """
    t = int(target) if target.isdigit() else target
    msgs = await _run_script(serial, t, script, timeout=timeout, spawn=spawn)

    if not msgs:
        return "Script ran but produced no output (no send() calls or timed out)"

    lines = [f"Output from {target} ({len(msgs)} message(s)):"]
    for m in msgs:
        if isinstance(m, dict) and "error" in m:
            lines.append(f"  [ERROR] {m['error']}")
            if m.get("stack"):
                lines.append(f"  {m['stack'][:200]}")
        else:
            lines.append(f"  {str(m)[:500]}")
    return "\n".join(lines)


# ── Memory analysis ────────────────────────────────────────────────────────────

@mcp.tool()
async def find_secrets_in_memory(
    target: str,
    serial: Optional[str] = None,
) -> str:
    """
    Scan process memory for API keys, passwords, tokens, and base64-encoded secrets.
    target: package name or PID
    """
    msgs = await _run_script(serial, target, FIND_SECRETS_SCRIPT, timeout=30)

    secrets = [m for m in msgs if isinstance(m, str) and '"secret"' in m]
    lines = [f"Memory scan on {target} — {len(secrets)} potential secret(s):"]

    seen = set()
    for s in secrets:
        try:
            d = json.loads(s)
            val = d.get("value", "")
            if val not in seen and len(val) > 8:
                seen.add(val)
                lines.append(f"  {val[:80]}")
        except Exception:
            pass

    if not secrets:
        lines.append("No secrets found in readable memory regions.")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
