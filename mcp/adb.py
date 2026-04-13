#!/usr/bin/env python3
"""
ADB MCP Server
Android Debug Bridge control via MCP tools for Hermes pentest profile.
Works with real devices (USB), Android emulators, and Docker-based emulators.
"""

import os
import asyncio
import shlex
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

mcp = FastMCP("adb")

ADB = os.environ.get("ADB_PATH", "adb")
PENTEST_OUTPUT = Path(os.environ.get("PENTEST_OUTPUT", "/pentest-output"))
DEFAULT_SERIAL = os.environ.get("ADB_SERIAL", "")  # e.g. "emulator-5554" or "192.168.x.x:5555"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run an adb command, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", f"Command timed out after {timeout}s"


def _serial_args(serial: Optional[str]) -> list[str]:
    s = serial or DEFAULT_SERIAL
    return ["-s", s] if s else []


async def _adb(serial: Optional[str], *args, timeout: int = 30) -> tuple[int, str, str]:
    return await _run([ADB] + _serial_args(serial) + list(args), timeout=timeout)


def _fmt(rc: int, stdout: str, stderr: str) -> str:
    if rc != 0 and not stdout.strip():
        return f"Error (rc={rc}): {stderr.strip() or 'unknown error'}"
    out = stdout.strip()
    if stderr.strip() and rc != 0:
        out += f"\n[stderr] {stderr.strip()}"
    return out or "(no output)"


# ── Device management ──────────────────────────────────────────────────────────

@mcp.tool()
async def list_devices() -> str:
    """List all connected ADB devices (real devices, emulators, TCP connections)."""
    rc, out, err = await _run([ADB, "devices", "-l"])
    lines = [l for l in out.strip().splitlines() if l and not l.startswith("List of")]
    if not lines:
        return (
            "No devices connected.\n\n"
            "To connect an emulator: adb_connect('192.168.x.x:5555') or start_emulator()\n"
            "To connect a real device: enable USB debugging, plug in, run list_devices() again."
        )
    return f"{len(lines)} device(s):\n" + "\n".join(f"  {l}" for l in lines)


@mcp.tool()
async def adb_connect(host_port: str) -> str:
    """
    Connect to an Android device or emulator over TCP/IP.
    host_port: e.g. '192.168.1.100:5555' or 'emulator-5554'
    """
    rc, out, err = await _run([ADB, "connect", host_port])
    return _fmt(rc, out, err)


@mcp.tool()
async def adb_disconnect(host_port: Optional[str] = None) -> str:
    """Disconnect from a TCP device. Omit host_port to disconnect all."""
    args = [ADB, "disconnect"]
    if host_port:
        args.append(host_port)
    rc, out, err = await _run(args)
    return _fmt(rc, out, err)


@mcp.tool()
async def device_info(serial: Optional[str] = None) -> str:
    """
    Get device properties: model, OS version, architecture, security patch, root status.
    serial: device serial (omit to use default/only device)
    """
    props = [
        "ro.product.model", "ro.product.manufacturer", "ro.build.version.release",
        "ro.build.version.sdk", "ro.product.cpu.abi", "ro.build.security_patch",
        "ro.debuggable", "ro.build.type", "ro.product.device",
    ]
    rc, out, err = await _adb(serial, "shell", "getprop")
    if rc != 0:
        return _fmt(rc, out, err)

    result = {}
    for line in out.splitlines():
        for p in props:
            if f"[{p}]" in line:
                val = line.split("]:")[-1].strip().strip("[]")
                result[p] = val

    # Check root
    rc2, root_out, _ = await _adb(serial, "shell", "su -c id 2>/dev/null || echo 'not-root'")
    rooted = "rooted" if "uid=0" in root_out else "not rooted"

    # Check SELinux
    rc3, selinux, _ = await _adb(serial, "shell", "getenforce 2>/dev/null || echo 'unknown'")

    lines = [
        f"Device:   {result.get('ro.product.manufacturer','')} {result.get('ro.product.model','')}",
        f"Android:  {result.get('ro.build.version.release','')} (SDK {result.get('ro.build.version.sdk','')})",
        f"ABI:      {result.get('ro.product.cpu.abi','')}",
        f"Security: {result.get('ro.build.security_patch','')}",
        f"Build:    {result.get('ro.build.type','')}  debuggable={result.get('ro.debuggable','')}",
        f"Root:     {rooted}",
        f"SELinux:  {selinux.strip()}",
    ]
    return "\n".join(lines)


# ── App management ─────────────────────────────────────────────────────────────

@mcp.tool()
async def list_packages(
    serial: Optional[str] = None,
    third_party_only: bool = True,
    filter: Optional[str] = None,
) -> str:
    """
    List installed packages on the device.
    third_party_only: True = only user-installed apps (default)
    filter: substring to match package names
    """
    args = ["shell", "pm", "list", "packages"]
    if third_party_only:
        args.append("-3")
    rc, out, err = await _adb(serial, *args)
    if rc != 0:
        return _fmt(rc, out, err)

    packages = [l.replace("package:", "").strip() for l in out.splitlines() if l.startswith("package:")]
    if filter:
        packages = [p for p in packages if filter.lower() in p.lower()]

    if not packages:
        return f"No packages found (filter={filter})"
    return f"{len(packages)} package(s):\n" + "\n".join(f"  {p}" for p in sorted(packages))


@mcp.tool()
async def get_app_info(package: str, serial: Optional[str] = None) -> str:
    """
    Get detailed info for an installed app: version, install path, permissions, activities.
    """
    rc, out, err = await _adb(serial, "shell", "dumpsys", "package", package)
    if rc != 0:
        return _fmt(rc, out, err)

    lines = out.splitlines()
    result = []

    # Extract key sections
    sections = ["versionName", "versionCode", "firstInstallTime", "lastUpdateTime",
                "dataDir", "codePath", "targetSdk", "flags"]
    for line in lines:
        for s in sections:
            if s in line:
                result.append(f"  {line.strip()}")
                break

    # Granted permissions
    in_perms = False
    perms = []
    for line in lines:
        if "grantedPermissions:" in line or "runtime permissions:" in line.lower():
            in_perms = True
        elif in_perms:
            if line.strip().startswith("android.permission"):
                perms.append(line.strip())
            elif line.strip() and not line.strip().startswith("android."):
                in_perms = False

    if result:
        result_str = "\n".join(result)
    else:
        result_str = out[:800]

    perm_str = f"\nGranted permissions ({len(perms)}):\n  " + "\n  ".join(perms[:15]) if perms else ""
    return f"Package: {package}\n{result_str}{perm_str}"


@mcp.tool()
async def install_apk(
    apk_path: str,
    serial: Optional[str] = None,
    allow_downgrade: bool = False,
    grant_permissions: bool = True,
) -> str:
    """
    Install an APK on the device.
    apk_path: path to APK on the server (e.g. /pentest-output/app.apk)
    """
    if not Path(apk_path).exists():
        return f"Error: APK not found: {apk_path}"

    args = ["install"]
    if allow_downgrade:
        args.append("-d")
    if grant_permissions:
        args.append("-g")
    args.append(apk_path)

    rc, out, err = await _adb(serial, *args, timeout=120)
    return _fmt(rc, out, err)


@mcp.tool()
async def uninstall_app(package: str, serial: Optional[str] = None, keep_data: bool = False) -> str:
    """Uninstall an app by package name."""
    args = ["uninstall"]
    if keep_data:
        args.append("-k")
    args.append(package)
    rc, out, err = await _adb(serial, *args)
    return _fmt(rc, out, err)


@mcp.tool()
async def start_app(
    package: str,
    activity: Optional[str] = None,
    serial: Optional[str] = None,
    extras: Optional[str] = None,
) -> str:
    """
    Launch an app. If activity is omitted, uses the main launcher activity.
    extras: optional intent extras e.g. '--es key value'
    """
    if activity:
        component = f"{package}/{activity}"
    else:
        # Resolve main activity
        rc, out, _ = await _adb(serial, "shell", "cmd", "package", "resolve-activity",
                                  "--brief", package)
        component = out.strip().splitlines()[-1] if out.strip() else package

    cmd = ["shell", "am", "start", "-n", component]
    if extras:
        cmd.extend(shlex.split(extras))

    rc, out, err = await _adb(serial, *cmd)
    return _fmt(rc, out, err)


@mcp.tool()
async def stop_app(package: str, serial: Optional[str] = None) -> str:
    """Force-stop an app."""
    rc, out, err = await _adb(serial, "shell", "am", "force-stop", package)
    return _fmt(rc, out, err)


@mcp.tool()
async def clear_app_data(package: str, serial: Optional[str] = None) -> str:
    """Clear an app's data and cache (resets to fresh install state)."""
    rc, out, err = await _adb(serial, "shell", "pm", "clear", package)
    return _fmt(rc, out, err)


@mcp.tool()
async def extract_apk(
    package: str,
    output_name: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """
    Pull the APK of an installed app to /pentest-output/ for static analysis.
    Returns the saved path for upload_file() in the MoBSF MCP.
    """
    # Find APK path on device
    rc, out, err = await _adb(serial, "shell", "pm", "path", package)
    if rc != 0 or "package:" not in out:
        return f"Package not found: {package}\n{err}"

    remote_path = out.strip().replace("package:", "")
    name = output_name or f"{package}.apk"
    local_path = PENTEST_OUTPUT / name

    rc2, out2, err2 = await _adb(serial, "pull", remote_path, str(local_path), timeout=60)
    if rc2 != 0:
        return _fmt(rc2, out2, err2)

    size_kb = local_path.stat().st_size // 1024
    return (
        f"APK extracted: {local_path}  ({size_kb} KB)\n"
        f"Remote: {remote_path}\n"
        "Next: use MoBSF upload_file() to scan it."
    )


# ── File system ────────────────────────────────────────────────────────────────

@mcp.tool()
async def shell(command: str, serial: Optional[str] = None, timeout: int = 30) -> str:
    """
    Run an arbitrary shell command on the device.
    command: shell command string (e.g. 'ls /data/local/tmp')
    """
    rc, out, err = await _adb(serial, "shell", command, timeout=timeout)
    return _fmt(rc, out, err)


@mcp.tool()
async def pull_file(
    remote_path: str,
    output_name: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """
    Pull a file from the device to /pentest-output/.
    remote_path: path on device (e.g. /sdcard/Download/file.db)
    """
    name = output_name or Path(remote_path).name
    local_path = PENTEST_OUTPUT / name
    rc, out, err = await _adb(serial, "pull", remote_path, str(local_path), timeout=60)
    if rc != 0:
        return _fmt(rc, out, err)
    size_kb = local_path.stat().st_size // 1024 if local_path.exists() else 0
    return f"Pulled: {local_path}  ({size_kb} KB)"


@mcp.tool()
async def push_file(local_path: str, remote_path: str, serial: Optional[str] = None) -> str:
    """
    Push a file from /pentest-output/ to the device.
    local_path: path on server (e.g. /pentest-output/payload.sh)
    remote_path: destination on device (e.g. /data/local/tmp/payload.sh)
    """
    if not Path(local_path).exists():
        return f"Error: local file not found: {local_path}"
    rc, out, err = await _adb(serial, "push", local_path, remote_path, timeout=60)
    return _fmt(rc, out, err)


@mcp.tool()
async def list_files(path: str, serial: Optional[str] = None) -> str:
    """List files at a path on the device (ls -la)."""
    rc, out, err = await _adb(serial, "shell", f"ls -la {shlex.quote(path)} 2>&1")
    return _fmt(rc, out, err)


@mcp.tool()
async def read_file(remote_path: str, serial: Optional[str] = None, max_bytes: int = 8192) -> str:
    """Read a text file from the device (first max_bytes bytes)."""
    rc, out, err = await _adb(serial, "shell", f"cat {shlex.quote(remote_path)} 2>&1")
    if rc != 0:
        return _fmt(rc, out, err)
    return out[:max_bytes] + (f"\n[truncated at {max_bytes} bytes]" if len(out) > max_bytes else "")


# ── Monitoring ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def logcat(
    package: Optional[str] = None,
    lines: int = 200,
    level: str = "V",
    serial: Optional[str] = None,
) -> str:
    """
    Get device logs.
    package: filter by package name (None = all)
    lines: number of lines to capture
    level: V | D | I | W | E (verbose to error)
    """
    # Get PID for package filter
    pid_filter = ""
    if package:
        rc, out, _ = await _adb(serial, "shell", f"pidof {package} 2>/dev/null")
        pid = out.strip()
        if pid:
            pid_filter = f"--pid={pid}"

    cmd = f"logcat -d -{level} {pid_filter} | tail -{lines}"
    rc, out, err = await _adb(serial, "shell", cmd, timeout=15)
    if not out.strip():
        return f"No logs found (package={package}, level={level})"
    return out


@mcp.tool()
async def screenshot(output_name: Optional[str] = None, serial: Optional[str] = None) -> str:
    """
    Take a screenshot and save to /pentest-output/.
    Returns MEDIA: path for Discord attachment.
    """
    name = output_name or "screenshot.png"
    local_path = PENTEST_OUTPUT / name
    remote_path = f"/data/local/tmp/{name}"

    # Capture on device
    rc, _, err = await _adb(serial, "shell", f"screencap -p {remote_path}")
    if rc != 0:
        return f"screencap failed: {err}"

    # Pull to host
    rc2, _, err2 = await _adb(serial, "pull", remote_path, str(local_path))
    if rc2 != 0:
        return f"pull failed: {err2}"

    # Clean up
    await _adb(serial, "shell", f"rm {remote_path}")

    size_kb = local_path.stat().st_size // 1024 if local_path.exists() else 0
    return f"Screenshot saved: {local_path}  ({size_kb} KB)\nMEDIA:{local_path}"


# ── Network & port forwarding ──────────────────────────────────────────────────

@mcp.tool()
async def forward_port(
    local_port: int,
    remote_port: int,
    serial: Optional[str] = None,
    protocol: str = "tcp",
) -> str:
    """
    Forward a local port to a port on the device.
    Use this to reach Frida server: forward_port(27042, 27042)
    """
    rc, out, err = await _adb(
        serial, "forward", f"{protocol}:{local_port}", f"{protocol}:{remote_port}"
    )
    return _fmt(rc, out or f"Forwarded localhost:{local_port} → device:{remote_port}", err)


@mcp.tool()
async def reverse_port(
    remote_port: int,
    local_port: int,
    serial: Optional[str] = None,
    protocol: str = "tcp",
) -> str:
    """
    Reverse-forward: device port → local port.
    Use this so the device can reach services on the server.
    """
    rc, out, err = await _adb(
        serial, "reverse", f"{protocol}:{remote_port}", f"{protocol}:{local_port}"
    )
    return _fmt(rc, out or f"Reversed device:{remote_port} → localhost:{local_port}", err)


@mcp.tool()
async def get_network_info(serial: Optional[str] = None) -> str:
    """Get IP addresses, WiFi info, and proxy settings on the device."""
    results = []

    rc, out, _ = await _adb(serial, "shell", "ip addr show wlan0 2>/dev/null | grep 'inet '")
    if out.strip():
        results.append(f"WiFi IP: {out.strip()}")

    rc, out, _ = await _adb(serial, "shell", "dumpsys wifi | grep -E 'mWifiInfo|SSID|IP' | head -5")
    if out.strip():
        results.append(f"WiFi info:\n{out.strip()}")

    rc, out, _ = await _adb(serial, "shell", "settings get global http_proxy 2>/dev/null")
    proxy = out.strip()
    results.append(f"HTTP proxy: {proxy if proxy and proxy != 'null' else 'none'}")

    rc, out, _ = await _adb(serial, "shell", "getprop net.dns1")
    results.append(f"DNS1: {out.strip()}")

    return "\n".join(results) if results else "Could not get network info"


# ── Security analysis ──────────────────────────────────────────────────────────

@mcp.tool()
async def device_security_check(serial: Optional[str] = None) -> str:
    """
    Security posture check: root, SELinux, encryption, developer options, USB debugging,
    install from unknown sources, screen lock.
    """
    checks = {}

    props = {
        "ro.debuggable": "Build debuggable",
        "ro.secure": "Build secure (0=insecure)",
        "ro.build.type": "Build type",
        "ro.crypto.state": "Encryption state",
        "persist.sys.usb.config": "USB config",
    }
    rc, out, _ = await _adb(serial, "shell", "getprop")
    for line in out.splitlines():
        for p, label in props.items():
            if f"[{p}]" in line:
                checks[label] = line.split("]:")[-1].strip().strip("[]")

    rc, selinux, _ = await _adb(serial, "shell", "getenforce 2>/dev/null")
    checks["SELinux"] = selinux.strip()

    rc, root, _ = await _adb(serial, "shell", "su -c id 2>/dev/null; echo $?")
    checks["Root"] = "YES" if "uid=0" in root else "no"

    rc, adb_enabled, _ = await _adb(serial, "shell", "settings get global adb_enabled")
    checks["USB Debugging"] = adb_enabled.strip()

    rc, unknown_src, _ = await _adb(serial, "shell",
        "settings get secure install_non_market_apps 2>/dev/null || "
        "settings get global install_non_market_apps 2>/dev/null")
    checks["Install unknown sources"] = unknown_src.strip()

    rc, lock, _ = await _adb(serial, "shell",
        "settings get system screen_lock_type 2>/dev/null || "
        "dumpsys devicepolicy | grep 'passwordQuality' | head -1")
    checks["Screen lock"] = lock.strip()[:60]

    lines = ["Security check:"]
    for k, v in checks.items():
        flag = "⚠" if (k == "Root" and v == "YES") or \
                     (k == "SELinux" and v.lower() == "permissive") or \
                     (k == "Build debuggable" and v == "1") else " "
        lines.append(f"  {flag} {k}: {v}")

    return "\n".join(lines)


@mcp.tool()
async def dump_content_providers(package: str, serial: Optional[str] = None) -> str:
    """
    Enumerate exported content providers for a package.
    Useful for finding IDOR / data exposure via content:// URIs.
    """
    rc, out, _ = await _adb(serial, "shell", f"dumpsys package {package} | grep -A2 'ContentProvider'")
    if not out.strip():
        rc, out, _ = await _adb(serial, "shell",
            f"aapt dump xmltree /data/app/{package}*/base.apk AndroidManifest.xml 2>/dev/null "
            f"| grep -A5 'provider'")
    return out[:3000] if out.strip() else f"No content providers found for {package}"


@mcp.tool()
async def query_content_provider(
    uri: str,
    serial: Optional[str] = None,
    projection: Optional[str] = None,
) -> str:
    """
    Query a content provider URI directly.
    uri: e.g. 'content://com.example.app/users'
    projection: columns to select (comma-separated, None = all)
    """
    cmd = f"content query --uri {uri}"
    if projection:
        cmd += f" --projection {projection}"
    rc, out, err = await _adb(serial, "shell", cmd)
    return _fmt(rc, out, err)


# ── Emulator helpers ───────────────────────────────────────────────────────────

@mcp.tool()
async def connect_emulator(host: str = "localhost", port: int = 5555) -> str:
    """
    Connect to an Android emulator running on the server or network.
    For docker-android: host='localhost', port=5555
    """
    rc, out, err = await _run([ADB, "connect", f"{host}:{port}"])
    return _fmt(rc, out, err)


if __name__ == "__main__":
    mcp.run()
