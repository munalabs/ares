#!/usr/bin/env python3
"""
MoBSF MCP Server
Mobile Security Framework via MCP tools for Hermes pentest profile.
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP

mcp = FastMCP("mobsf")

MOBSF_URL = os.environ.get("MOBSF_URL", "http://172.17.0.1:8100").rstrip("/")
MOBSF_API_KEY = os.environ.get("MOBSF_API_KEY", "")
PENTEST_OUTPUT = Path(os.environ.get("PENTEST_OUTPUT", "/pentest-output"))

AUTH_HEADER = {"Authorization": MOBSF_API_KEY}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=MOBSF_URL,
        headers=AUTH_HEADER,
        timeout=120.0,
    )


# ── Scan management ────────────────────────────────────────────────────────────

@mcp.tool()
async def list_scans(page: int = 1, page_size: int = 20) -> str:
    """List all scans in MoBSF with app name, package, type, hash, and timestamp."""
    async with _client() as c:
        r = await c.get(f"/api/v1/scans?page={page}&page_size={page_size}")
        r.raise_for_status()
        data = r.json()

    scans = data.get("content", [])
    if not scans:
        return "No scans found."

    lines = [f"Total: {data.get('count', 0)} scan(s)\n"]
    for s in scans:
        lines.append(
            f"  [{s['SCAN_TYPE'].upper()}] {s['APP_NAME']} ({s['PACKAGE_NAME']})\n"
            f"         File: {s['FILE_NAME']}\n"
            f"         Hash: {s['MD5']}\n"
            f"         Time: {s['TIMESTAMP']}\n"
        )
    return "\n".join(lines)


@mcp.tool()
async def delete_scan(file_hash: str) -> str:
    """Delete a scan and all its data from MoBSF by MD5 hash."""
    async with _client() as c:
        r = await c.post("/api/v1/delete_scan", data={"hash": file_hash})
        r.raise_for_status()
        return r.json().get("deleted", r.text)


# ── Upload & scan ──────────────────────────────────────────────────────────────

@mcp.tool()
async def upload_file(file_path: str) -> str:
    """
    Upload an APK, IPA, or ZIP to MoBSF for analysis.
    Returns the file hash needed for subsequent scan/report calls.
    file_path: absolute path on the server (e.g. /pentest-output/app.apk)
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"

    async with _client() as c:
        with open(path, "rb") as fh:
            r = await c.post(
                "/api/v1/upload",
                files={"file": (path.name, fh, "application/octet-stream")},
            )
        r.raise_for_status()
        data = r.json()

    if "hash" not in data:
        return f"Upload failed: {data}"

    return (
        f"Uploaded: {data.get('file_name')}\n"
        f"Hash:     {data['hash']}\n"
        f"Type:     {data.get('scan_type', 'unknown')}\n"
        "Next: call start_scan(file_hash) to begin analysis."
    )


@mcp.tool()
async def start_scan(
    file_hash: str,
    scan_type: str = "apk",
    rescan: int = 0,
) -> str:
    """
    Start static analysis on an uploaded file.
    scan_type: apk | ipa | zip | appx
    rescan: 1 to force re-scan, 0 to use cached results
    """
    async with _client() as c:
        r = await c.post(
            "/api/v1/scan",
            data={"hash": file_hash, "scan_type": scan_type, "re_scan": rescan},
        )
        r.raise_for_status()
        data = r.json()

    if "analyzer" in data:
        return (
            f"Scan started: {data.get('app_name', file_hash)}\n"
            f"Analyzer: {data['analyzer']}\n"
            f"Status: {data.get('status', 'running')}\n"
            "Next: call get_report(file_hash) once complete."
        )
    return json.dumps(data, indent=2)


# ── Reports ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_report(file_hash: str) -> str:
    """
    Get the full static analysis summary for a scan.
    Returns severity counts, dangerous permissions, trackers, and CVSS.
    Use get_findings() for the detailed finding list.
    """
    async with _client() as c:
        r = await c.post("/api/v1/report_json", data={"hash": file_hash})
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        return f"Error: {data['error']}"

    app = data.get("app_name", "unknown")
    pkg = data.get("package_name", "")
    version = data.get("version_name", "")
    app_type = data.get("app_type", data.get("file_name", ""))

    # appsec has high/warning/info/secure arrays
    appsec = data.get("appsec", {})
    high_count = len(appsec.get("high", []))
    warn_count = len(appsec.get("warning", []))
    info_count = len(appsec.get("info", []))
    secure_count = len(appsec.get("secure", []))

    permissions = data.get("permissions", {})
    dangerous_perms = [k for k, v in permissions.items() if isinstance(v, dict) and v.get("status") == "dangerous"]

    trackers = data.get("trackers", {})
    tracker_count = trackers.get("detected_trackers", 0) if isinstance(trackers, dict) else 0

    cvss = data.get("average_cvss") or "N/A"

    return (
        f"App:      {app} ({pkg}) v{version} [{app_type}]\n"
        f"Hash:     {file_hash}\n"
        f"CVSS:     {cvss}\n\n"
        f"Findings:  HIGH={high_count}  WARNING={warn_count}  INFO={info_count}  SECURE={secure_count}\n"
        f"Dangerous permissions: {len(dangerous_perms)}\n"
        f"  {', '.join(dangerous_perms[:10])}\n"
        f"Trackers: {tracker_count}\n\n"
        "Use get_findings(hash) for the full finding list.\n"
        "Use generate_pdf(hash) to produce a deliverable report."
    )


@mcp.tool()
async def get_findings(
    file_hash: str,
    severity: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """
    Get filtered findings from a static analysis report.
    severity: high | warning | info | secure  (None = high+warning only)
    category: any section name e.g. code | manifest | certificate | network (None = all)
    """
    async with _client() as c:
        r = await c.post("/api/v1/scorecard", data={"hash": file_hash})
        r.raise_for_status()
        scorecard = r.json()

    if "error" in scorecard:
        return f"Error: {scorecard['error']}"

    # scorecard structure: {"high": [{title, description, section}...], "warning": [...], ...}
    severities = ["high", "warning", "info"] if severity is None else [severity]

    findings = []
    for sev in severities:
        for item in scorecard.get(sev, []):
            sec = item.get("section", "")
            if category and category.lower() not in sec.lower():
                continue
            findings.append(
                f"[{sev.upper()}] {item.get('title', '')}\n"
                f"  Section: {sec}\n"
                f"  {item.get('description', '')[:200]}"
            )

    if not findings:
        return f"No findings matching severity={severity} category={category}"

    return f"{len(findings)} finding(s):\n\n" + "\n\n".join(findings)


@mcp.tool()
async def get_scorecard(file_hash: str) -> str:
    """Get the security scorecard summary (HIGH/WARNING/INFO counts + top issues)."""
    async with _client() as c:
        r = await c.post("/api/v1/scorecard", data={"hash": file_hash})
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        return f"Error: {data['error']}"

    high = data.get("high", [])
    warning = data.get("warning", [])
    info = data.get("info", [])
    secure = data.get("secure", [])

    lines = [
        f"HIGH: {len(high)}  WARNING: {len(warning)}  INFO: {len(info)}  SECURE: {len(secure)}",
        "",
        "── HIGH ──",
    ]
    for item in high[:5]:
        lines.append(f"  • [{item.get('section','')}] {item.get('title','')}")
    if len(high) > 5:
        lines.append(f"  ... and {len(high)-5} more")

    lines.append("\n── WARNING (top 5) ──")
    for item in warning[:5]:
        lines.append(f"  • [{item.get('section','')}] {item.get('title','')}")
    if len(warning) > 5:
        lines.append(f"  ... and {len(warning)-5} more")

    return "\n".join(lines)


@mcp.tool()
async def generate_pdf(file_hash: str, output_name: Optional[str] = None) -> str:
    """
    Generate a PDF report for a scan and save it to /pentest-output/.
    Returns the saved file path for MEDIA: attachment.
    output_name: optional filename (default: mobsf-{hash[:8]}.pdf)
    """
    name = output_name or f"mobsf-{file_hash[:8]}.pdf"
    out_path = PENTEST_OUTPUT / name

    async with _client() as c:
        r = await c.post("/api/v1/download_pdf", data={"hash": file_hash})
        r.raise_for_status()
        out_path.write_bytes(r.content)

    size_kb = out_path.stat().st_size // 1024
    return f"PDF saved: {out_path}  ({size_kb} KB)\nMEDIA:{out_path}"


@mcp.tool()
async def generate_json_report(file_hash: str, output_name: Optional[str] = None) -> str:
    """
    Save the full JSON report to /pentest-output/ for further processing.
    Returns the saved file path.
    """
    name = output_name or f"mobsf-{file_hash[:8]}-report.json"
    out_path = PENTEST_OUTPUT / name

    async with _client() as c:
        r = await c.post("/api/v1/report_json", data={"hash": file_hash})
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        return f"Error: {data['error']}"

    out_path.write_text(json.dumps(data, indent=2))
    size_kb = out_path.stat().st_size // 1024
    return f"JSON report saved: {out_path}  ({size_kb} KB)"


# ── Dynamic analysis ───────────────────────────────────────────────────────────

@mcp.tool()
async def list_devices() -> str:
    """List Android/iOS devices available for dynamic analysis in MoBSF."""
    async with _client() as c:
        r = await c.get("/api/v1/dynamic/get_apps")
        if r.status_code == 200:
            return r.text
        return f"No devices connected or dynamic analysis not configured (HTTP {r.status_code})"


@mcp.tool()
async def start_dynamic_analysis(
    file_hash: str,
    apk_path: Optional[str] = None,
) -> str:
    """
    Start dynamic analysis for an APK on a connected Android emulator/device.
    Requires MoBSF dynamic analysis setup (Android emulator or real device via ADB).
    file_hash: MD5 hash from upload/scan
    """
    payload: dict = {"hash": file_hash}
    if apk_path:
        payload["apk_path"] = apk_path

    async with _client() as c:
        r = await c.post("/api/v1/dynamic/start_analysis", data=payload)
        data = r.json()

    if "error" in data:
        return f"Error: {data['error']}"
    return json.dumps(data, indent=2)


@mcp.tool()
async def stop_dynamic_analysis(file_hash: str) -> str:
    """Stop dynamic analysis and trigger report generation."""
    async with _client() as c:
        r = await c.post("/api/v1/dynamic/stop_analysis", data={"hash": file_hash})
        return r.text


@mcp.tool()
async def get_dynamic_report(file_hash: str) -> str:
    """Get dynamic analysis results (traffic, API calls, file activity) for a scan."""
    async with _client() as c:
        r = await c.post("/api/v1/dynamic/report_json", data={"hash": file_hash})
        if r.status_code != 200:
            return f"Dynamic report not available (HTTP {r.status_code}). Run dynamic analysis first."
        data = r.json()

    if "error" in data:
        return f"Error: {data['error']}"

    # Summarise
    net = data.get("network", {})
    urls = net.get("urls_list", [])
    apis = data.get("android_api", {})
    activities = data.get("activities_invoked", [])

    return (
        f"Dynamic analysis results for {file_hash[:8]}\n\n"
        f"Network requests: {len(urls)}\n"
        f"Android API calls: {len(apis)}\n"
        f"Activities invoked: {len(activities)}\n\n"
        f"Top URLs:\n" + "\n".join(f"  {u}" for u in urls[:10]) +
        "\n\nUse generate_pdf(hash) for full dynamic report."
    )


# ── Search ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_scans(query: str) -> str:
    """
    Search scans by app name, package name, or file name.
    query: string to match against app/package/filename
    """
    async with _client() as c:
        # MoBSF doesn't have a search endpoint — fetch all and filter
        r = await c.get("/api/v1/scans?page=1&page_size=100")
        r.raise_for_status()
        data = r.json()

    q = query.lower()
    matches = [
        s for s in data.get("content", [])
        if q in s.get("APP_NAME", "").lower()
        or q in s.get("PACKAGE_NAME", "").lower()
        or q in s.get("FILE_NAME", "").lower()
    ]

    if not matches:
        return f"No scans matching '{query}'"

    lines = [f"{len(matches)} match(es) for '{query}':\n"]
    for s in matches:
        lines.append(
            f"  [{s['SCAN_TYPE'].upper()}] {s['APP_NAME']} — {s['PACKAGE_NAME']}\n"
            f"         Hash: {s['MD5']}  |  {s['TIMESTAMP']}\n"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
