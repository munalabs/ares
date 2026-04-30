"""Mobile APK/IPA analysis pipeline for Ares.

Static analysis via MoBSF + optional dynamic analysis via ADB + Frida.
Both emulator and physical devices are supported — both exposed to muna1
via the Cloudflare Tunnel mobile bridge (setup-mobile-tunnel.sh).

Environment variables (set by dev/.mobile-tunnel.env or muna1 systemd env):
  MOBSF_URL              — MoBSF base URL (default: http://localhost:8100)
  MOBSF_API_KEY          — MoBSF REST API key
  ANDROID_ADB_SERVER_HOST — ADB server host (default: 127.0.0.1)
  ANDROID_ADB_SERVER_PORT — ADB server port (default: 5038 via CF tunnel)
  FRIDA_TCP_HOST          — Frida TCP host (default: 127.0.0.1)
  FRIDA_TCP_PORT          — Frida TCP port (default: 27042 via CF tunnel)
  ARES_DYNAMIC_ANALYSIS   — "true" to enable dynamic analysis (default: false)
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

from muna_agentsdk import Finding, JobResult, JobSpec, MobileTarget, Observation

logger = logging.getLogger(__name__)

_MOBSF_URL = os.getenv("MOBSF_URL", "http://localhost:8100")
_MOBSF_API_KEY = os.getenv("MOBSF_API_KEY", "")
_ADB_HOST = os.getenv("ANDROID_ADB_SERVER_HOST", "127.0.0.1")
_ADB_PORT = int(os.getenv("ANDROID_ADB_SERVER_PORT", "5038"))
_FRIDA_HOST = os.getenv("FRIDA_TCP_HOST", "127.0.0.1")
_FRIDA_PORT = int(os.getenv("FRIDA_TCP_PORT", "27042"))
_DYNAMIC_ENABLED = os.getenv("ARES_DYNAMIC_ANALYSIS", "").lower() in ("1", "true", "yes")

_SEVERITY_MAP = {
    "high": "high", "warning": "medium", "info": "low",
    "secure": "info", "hotspot": "medium",
}


class MobileAnalyzer:
    """Orchestrates MoBSF static + optional ADB/Frida dynamic analysis."""

    def __init__(self, spec: JobSpec) -> None:
        self.spec = spec
        assert isinstance(spec.target, MobileTarget)
        self.target: MobileTarget = spec.target

    async def run(self) -> JobResult:
        """Download artifact, run static (always) + dynamic (if enabled), return result."""
        import time
        t0 = time.monotonic()

        # Step 1 — download artifact from Argos-provided URL
        logger.info(f"Downloading artifact: {self.target.artifact_url}")
        try:
            artifact_bytes = await _download_artifact(self.target.artifact_url)
        except Exception as e:
            return _error_result(self.spec, f"Artifact download failed: {e}")

        suffix = ".apk" if self.target.platform == "android" else ".ipa"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(artifact_bytes)
            artifact_path = Path(f.name)

        try:
            findings, observations = await self._analyze(artifact_path)
        finally:
            artifact_path.unlink(missing_ok=True)

        duration = time.monotonic() - t0
        return JobResult(
            job_id=self.spec.job_id,
            tenant_id=self.spec.tenant_id,
            status="completed",
            findings=tuple(findings),
            observations=tuple(observations) if observations else None,
            cost_usd=0.0,
            duration_s=duration,
        )

    async def _analyze(
        self, artifact_path: Path
    ) -> tuple[list[Finding], list[Observation]]:
        findings: list[Finding] = []
        observations: list[Observation] = []

        # Static analysis (always)
        logger.info(f"MoBSF static analysis: {artifact_path.name}")
        try:
            static_findings, static_obs = await _mobsf_static(
                artifact_path, self.target.platform, self.spec.job_id
            )
            findings.extend(static_findings)
            observations.extend(static_obs)
        except Exception as e:
            logger.error(f"MoBSF static analysis failed: {e}")
            observations.append(Observation(
                id=f"obs-mobsf-error-{self.spec.job_id[:8]}",
                job_id=self.spec.job_id,
                kind="informational",
                description=f"MoBSF static analysis failed: {e}",
            ))

        # Dynamic analysis (optional — requires emulator/device via ADB bridge)
        if _DYNAMIC_ENABLED and self.target.platform == "android":
            logger.info("ADB/Frida dynamic analysis enabled")
            try:
                dyn_findings, dyn_obs = await _dynamic_android(
                    artifact_path, self.spec.job_id
                )
                findings.extend(dyn_findings)
                observations.extend(dyn_obs)
            except Exception as e:
                logger.warning(f"Dynamic analysis failed (non-fatal): {e}")
                observations.append(Observation(
                    id=f"obs-dynamic-error-{self.spec.job_id[:8]}",
                    job_id=self.spec.job_id,
                    kind="informational",
                    description=f"Dynamic analysis skipped: {e}",
                ))

        return findings, observations


# ---------------------------------------------------------------------------
# MoBSF static analysis
# ---------------------------------------------------------------------------

async def _mobsf_static(
    artifact_path: Path,
    platform: str,
    job_id: str,
) -> tuple[list[Finding], list[Observation]]:
    """Upload APK/IPA to MoBSF, trigger scan, parse findings."""
    if not _MOBSF_API_KEY:
        raise ValueError("MOBSF_API_KEY not set")

    headers = {"Authorization": _MOBSF_API_KEY}
    base = _MOBSF_URL.rstrip("/")

    async with httpx.AsyncClient(timeout=300) as client:
        # Upload
        with artifact_path.open("rb") as f:
            upload_resp = await client.post(
                f"{base}/api/v1/upload",
                headers=headers,
                files={"file": (artifact_path.name, f, "application/octet-stream")},
            )
        upload_resp.raise_for_status()
        file_hash = upload_resp.json().get("hash")
        if not file_hash:
            raise ValueError(f"MoBSF upload failed: {upload_resp.text[:200]}")

        logger.debug(f"MoBSF upload hash: {file_hash}")

        # Scan
        scan_resp = await client.post(
            f"{base}/api/v1/scan",
            headers=headers,
            data={"hash": file_hash, "re_scan": "0"},
        )
        scan_resp.raise_for_status()

        # Get report
        report_resp = await client.post(
            f"{base}/api/v1/report_json",
            headers=headers,
            data={"hash": file_hash},
        )
        report_resp.raise_for_status()
        report = report_resp.json()

    return _parse_mobsf_report(report, job_id)


def _parse_mobsf_report(
    report: dict[str, Any], job_id: str
) -> tuple[list[Finding], list[Observation]]:
    findings: list[Finding] = []
    observations: list[Observation] = []

    appsec = report.get("appsec", {})

    # High and warning level findings → Finding
    for level in ("high", "warning"):
        for item in appsec.get(level, []):
            title = item.get("title", "Unknown")
            desc = item.get("description", item.get("desc", ""))
            severity = "high" if level == "high" else "medium"
            findings.append(Finding(
                id=f"f-mobsf-{level}-{abs(hash(title)):08x}",
                job_id=job_id,
                finding_type="vulnerability",
                severity=severity,  # type: ignore[arg-type]
                title=title[:200],
                description=desc[:2000],
                evidence=f"MoBSF static analysis — {level} severity",
                metadata={"source": "mobsf", "level": level, "section": item.get("section", "")},
            ))

    # Info + secure → Observation
    for level in ("info", "secure"):
        for item in appsec.get(level, []):
            observations.append(Observation(
                id=f"obs-mobsf-{level}-{abs(hash(item.get('title',''))):08x}",
                job_id=job_id,
                kind="informational",
                description=f"[{level.upper()}] {item.get('title', '')}",
                metadata={"source": "mobsf", "level": level},
            ))

    # Permissions analysis
    perms = report.get("permissions", {})
    dangerous = [p for p, v in perms.items() if isinstance(v, dict) and v.get("status") == "dangerous"]
    if dangerous:
        observations.append(Observation(
            id=f"obs-mobsf-perms-{job_id[:8]}",
            job_id=job_id,
            kind="informational",
            description=f"Dangerous permissions declared: {', '.join(dangerous[:10])}",
            metadata={"source": "mobsf", "dangerous_permissions": dangerous},
        ))

    logger.info(
        f"MoBSF report parsed: {len(findings)} findings, {len(observations)} observations"
    )
    return findings, observations


# ---------------------------------------------------------------------------
# Dynamic analysis (ADB + Frida)
# ---------------------------------------------------------------------------

async def _dynamic_android(
    artifact_path: Path,
    job_id: str,
) -> tuple[list[Finding], list[Observation]]:
    """Install APK on emulator/device via ADB, hook with Frida, collect findings."""
    import asyncio
    import subprocess

    findings: list[Finding] = []
    observations: list[Observation] = []

    adb = ["adb", "-H", _ADB_HOST, "-P", str(_ADB_PORT)]

    # Check device is available
    result = subprocess.run(adb + ["devices"], capture_output=True, text=True, timeout=15)
    lines = [l for l in result.stdout.splitlines() if "\t" in l and "offline" not in l]
    if not lines:
        raise RuntimeError(
            f"No ADB device available at {_ADB_HOST}:{_ADB_PORT}. "
            "Is the mobile bridge running on muna1?"
        )
    device_serial = lines[0].split("\t")[0].strip()
    logger.info(f"Dynamic analysis on device: {device_serial}")

    adb_device = adb + ["-s", device_serial]

    # Install APK
    logger.info("Installing APK on device...")
    install = subprocess.run(
        adb_device + ["install", "-t", "-r", str(artifact_path)],
        capture_output=True, text=True, timeout=120,
    )
    if install.returncode != 0:
        raise RuntimeError(f"APK install failed: {install.stderr[:300]}")

    # Get package name from APK (via aapt if available, else skip)
    pkg_name = _get_package_name(artifact_path)
    if not pkg_name:
        observations.append(Observation(
            id=f"obs-pkg-unknown-{job_id[:8]}",
            job_id=job_id,
            kind="informational",
            description="Could not determine APK package name — Frida hooking skipped",
        ))
        return findings, observations

    # Launch app
    subprocess.run(
        adb_device + ["shell", "monkey", "-p", pkg_name, "-c",
                      "android.intent.category.LAUNCHER", "1"],
        capture_output=True, timeout=15,
    )
    await asyncio.sleep(3)

    # Frida: bypass SSL pinning + collect network traffic observations
    try:
        frida_obs = await _frida_hook(device_serial, pkg_name, job_id)
        observations.extend(frida_obs)
    except Exception as e:
        logger.warning(f"Frida hook failed: {e}")

    # Cleanup
    subprocess.run(adb_device + ["shell", "am", "force-stop", pkg_name],
                   capture_output=True, timeout=10)
    subprocess.run(adb_device + ["uninstall", pkg_name],
                   capture_output=True, timeout=30)

    return findings, observations


async def _frida_hook(device_serial: str, pkg_name: str, job_id: str) -> list[Observation]:
    """Connect to Frida via TCP bridge, inject SSL pinning bypass, collect observations."""
    import asyncio

    try:
        import frida
    except ImportError:
        return [Observation(
            id=f"obs-frida-missing-{job_id[:8]}",
            job_id=job_id,
            kind="informational",
            description="frida Python package not installed — dynamic hooking skipped",
        )]

    observations: list[Observation] = []

    # Connect to Frida via TCP (the CF tunnel exposes frida-server port)
    mgr = frida.get_device_manager()
    try:
        device = mgr.add_remote_device(f"{_FRIDA_HOST}:{_FRIDA_PORT}")
    except Exception as e:
        return [Observation(
            id=f"obs-frida-connect-{job_id[:8]}",
            job_id=job_id,
            kind="informational",
            description=f"Frida connection failed ({_FRIDA_HOST}:{_FRIDA_PORT}): {e}",
        )]

    # SSL pinning bypass script (OkHttp3 + TrustManager)
    bypass_script = """
    Java.perform(function() {
        // OkHttp3 CertificatePinner bypass
        try {
            var CertificatePinner = Java.use('okhttp3.CertificatePinner');
            CertificatePinner.check.overload('java.lang.String', 'java.util.List')
                .implementation = function() { return; };
            send({type: 'ssl_bypass', method: 'okhttp3'});
        } catch(e) {}

        // TrustManager bypass
        try {
            var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
            TrustManagerImpl.checkTrustedRecursive.implementation = function() { return; };
            send({type: 'ssl_bypass', method: 'trustmanager'});
        } catch(e) {}
    });
    """

    bypasses: list[str] = []

    def on_message(message, data):
        if message.get("type") == "send":
            payload = message.get("payload", {})
            if payload.get("type") == "ssl_bypass":
                bypasses.append(payload.get("method", "unknown"))

    try:
        session = device.attach(pkg_name)
        script = session.create_script(bypass_script)
        script.on("message", on_message)
        script.load()
        await asyncio.sleep(5)  # let it run briefly
        script.unload()
        session.detach()
    except Exception as e:
        return [Observation(
            id=f"obs-frida-attach-{job_id[:8]}",
            job_id=job_id,
            kind="informational",
            description=f"Frida attach failed for {pkg_name}: {e}",
        )]

    if bypasses:
        observations.append(Observation(
            id=f"obs-ssl-pinning-bypassed-{job_id[:8]}",
            job_id=job_id,
            kind="informational",
            description=(
                f"SSL pinning bypassed via Frida ({', '.join(bypasses)}). "
                "Network traffic can be intercepted in a MITM attack. "
                "Consider adding certificate pinning validation."
            ),
            metadata={"frida_bypass_methods": bypasses},
        ))

    return observations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIVATE_IP_RE = re.compile(
    r"^("
    r"10\."                         # RFC 1918
    r"|172\.(1[6-9]|2\d|3[01])\."  # RFC 1918
    r"|192\.168\."                  # RFC 1918
    r"|127\."                       # loopback IPv4
    r"|169\.254\."                  # link-local / cloud metadata
    r"|::1$"                        # IPv6 loopback
    r"|::ffff:"                     # IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    r"|0:0:0:0:0:ffff:"             # alternate IPv4-mapped notation
    r"|localhost$"                  # hostname
    r")",
    re.IGNORECASE,
)


def _validate_artifact_url(url: str) -> None:
    """Reject artifact URLs that could be used for SSRF attacks.

    artifact_url comes from Argos (trusted), but a compromised Argos or a
    malicious JobSpec could point it at internal services or cloud metadata.
    Hard-block private IPs, loopback, cloud metadata, and IPv4-mapped IPv6.

    DNS rebinding note: this validates the hostname string only. A DNS rebinding
    attack (public IP at validation time → private IP at connection time) would
    bypass this. For full protection, deploy Argos with an egress firewall that
    blocks RFC 1918 ranges at the network level.
    """
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"artifact_url scheme must be http/https, got: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if _PRIVATE_IP_RE.match(host):
        raise ValueError(f"artifact_url points to a private/reserved address: {host!r}")
    if host in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError(f"artifact_url points to cloud metadata endpoint: {host!r}")


async def _download_artifact(url: str) -> bytes:
    _validate_artifact_url(url)
    async with httpx.AsyncClient(timeout=300, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _get_package_name(apk_path: Path) -> str | None:
    """Extract Android package name using aapt or aapt2 if available."""
    import subprocess
    for tool in ("aapt", "aapt2"):
        try:
            r = subprocess.run(
                [tool, "dump", "badging", str(apk_path)],
                capture_output=True, text=True, timeout=30,
            )
            for line in r.stdout.splitlines():
                if line.startswith("package: name="):
                    # package: name='com.example.app' versionCode=...
                    parts = line.split("'")
                    if len(parts) >= 2:
                        return parts[1]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _error_result(spec: JobSpec, error: str) -> JobResult:
    return JobResult(
        job_id=spec.job_id,
        tenant_id=spec.tenant_id,
        status="failed",
        cost_usd=0.0,
        duration_s=0.0,
        error=error,
    )
