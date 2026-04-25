"""Type adapters: muna-agentsdk JobSpec ↔ Hermes brief ↔ muna-agentsdk JobResult.

Ares speaks in free-form pentest briefs and produces per-finding JSON state files.
These adapters translate at the boundary without changing Hermes internals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from muna_agentsdk import (
    DynamicTarget,
    Finding,
    JobResult,
    JobSpec,
    MobileTarget,
    Observation,
    PatchDiff,
    StaticTarget,
)

_SEVERITY_MAP: dict[str, str] = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "INFO": "info",
    "INFORMATIONAL": "info",
}

# Default pentest output root
_DEFAULT_PENTEST_OUTPUT = os.path.expanduser("~/pentest-output")


def pentest_output_dir() -> Path:
    return Path(os.getenv("ARES_PENTEST_OUTPUT", _DEFAULT_PENTEST_OUTPUT))


def engagement_dir(job_id: str) -> Path:
    return pentest_output_dir() / job_id


# ---------------------------------------------------------------------------
# JobSpec → Hermes brief
# ---------------------------------------------------------------------------

def build_brief(spec: JobSpec) -> str:
    """Build a human-readable pentest brief from a muna-agentsdk JobSpec."""
    target = spec.target

    if isinstance(target, DynamicTarget):
        scope_line = f"Scope: {target.scope}"
        target_line = f"Full web app assessment on {target.base_url}"
        creds_line = "Auth: credentials are available via Vault (see env ARES_AUTH_CONTEXT)"
    elif isinstance(target, MobileTarget):
        target_line = f"Mobile app security assessment — platform: {target.platform.upper()}"
        scope_line = f"Artifact: {target.artifact_url}"
        creds_line = ""
    elif isinstance(target, StaticTarget):
        target_line = f"Static code security review for {target.repo_url}"
        scope_line = f"Commit: {target.commit_sha}"
        creds_line = ""
    else:
        target_line = "Security assessment"
        scope_line = ""
        creds_line = ""

    diff_context = ""
    if spec.diff and spec.diff.changed_endpoints:
        endpoints = ", ".join(spec.diff.changed_endpoints[:10])
        diff_context = f"\nFocus on recently changed endpoints: {endpoints}"
    elif spec.diff and spec.diff.changed_files:
        files = ", ".join(spec.diff.changed_files[:10])
        diff_context = f"\nRecently changed files: {files}"

    parts = [
        target_line,
        scope_line,
        creds_line,
        diff_context,
        f"\nEngagement ID: {spec.job_id}",
        f"Budget remaining: ${spec.budget_remaining_usd:.2f}",
        "Destructive: no",
        "Go.",
    ]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Engagement output → muna-agentsdk JobResult
# ---------------------------------------------------------------------------

def read_engagement_result(spec: JobSpec, duration_s: float) -> JobResult:
    """Read Hermes output files and build a muna-agentsdk JobResult."""
    out_dir = engagement_dir(spec.job_id)
    findings, observations = _parse_findings(out_dir, spec.job_id)
    cost_usd = _read_cost(out_dir)

    return JobResult(
        job_id=spec.job_id,
        tenant_id=spec.tenant_id,
        status="completed",
        cost_usd=cost_usd,
        duration_s=duration_s,
        findings=tuple(findings),
        observations=tuple(observations) if observations else None,
    )


def _parse_findings(out_dir: Path, job_id: str) -> tuple[list[Finding], list[Observation]]:
    """Parse verified findings from the verification loop state files."""
    findings: list[Finding] = []
    observations: list[Observation] = []

    verification_dir = out_dir / "verification" / "findings"
    if not verification_dir.exists():
        # Fallback: try findings-raw.json
        raw_path = out_dir / "findings-raw.json"
        if raw_path.exists():
            findings.extend(_parse_raw_findings(raw_path, job_id))
        return findings, observations

    for state_file in sorted(verification_dir.glob("finding-*-state.json")):
        try:
            state = json.loads(state_file.read_text())
            decision = state.get("final_decision", "")

            if decision == "VERIFIED":
                findings.append(_state_to_finding(state, job_id))
            elif decision == "REJECTED":
                pass  # Filtered — false positives not reported
            else:
                # ESCALATED or in-loop — include as observation
                observations.append(_state_to_observation(state, job_id))
        except Exception:
            continue

    return findings, observations


def _state_to_finding(state: dict[str, Any], job_id: str) -> Finding:
    finding_id = state.get("finding_id", f"f-{hash(state.get('claim',''))&0xFFFF:04x}")
    severity_raw = state.get("severity_claimed", "MEDIUM").upper()
    severity = _SEVERITY_MAP.get(severity_raw, "medium")

    # Extract PoC from history
    poc: str | None = None
    for entry in state.get("history", []):
        if entry.get("role") == "finder" and "curl" in entry.get("content", "").lower():
            poc = entry["content"][:2000]
            break

    return Finding(
        id=finding_id,
        job_id=job_id,
        finding_type="vulnerability",
        severity=severity,  # type: ignore[arg-type]
        title=state.get("claim", "Untitled finding")[:200],
        description=state.get("final_decision_reasoning", state.get("claim", "")),
        evidence=state.get("claim", ""),
        endpoint=state.get("target_url"),
        poc=poc,
        metadata={
            "engagement_id": state.get("engagement_id"),
            "iterations": len(state.get("history", [])),
            "verification_file": str(state.get("session_id", "")),
        },
    )


def _state_to_observation(state: dict[str, Any], job_id: str) -> Observation:
    return Observation(
        id=state.get("finding_id", f"obs-{hash(state.get('claim',''))&0xFFFF:04x}"),
        job_id=job_id,
        kind="informational",
        description=f"[ESCALATED] {state.get('claim', '')}",
        url=state.get("target_url"),
        metadata={"escalation_reason": state.get("escalation_reason", "")},
    )


def _parse_raw_findings(raw_path: Path, job_id: str) -> list[Finding]:
    """Fallback: parse findings-raw.json if verification loop didn't run."""
    findings = []
    with open(raw_path) as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                severity_raw = d.get("severity", "MEDIUM").upper()
                findings.append(Finding(
                    id=d.get("id", f"f-raw-{i:04d}"),
                    job_id=job_id,
                    finding_type="vulnerability",
                    severity=_SEVERITY_MAP.get(severity_raw, "medium"),  # type: ignore[arg-type]
                    title=d.get("title", "Finding")[:200],
                    description=d.get("description", ""),
                    evidence=d.get("evidence", ""),
                    endpoint=d.get("endpoint"),
                    metadata={"source": "raw", "phase": d.get("phase")},
                ))
            except Exception:
                continue
    return findings


def _read_cost(out_dir: Path) -> float:
    """Try to read cost from engagement metadata."""
    meta_path = out_dir / "engagement-metadata.json"
    if meta_path.exists():
        try:
            d = json.loads(meta_path.read_text())
            return float(d.get("total_cost_usd", 0.0))
        except Exception:
            pass
    return 0.0


def is_engagement_complete(job_id: str) -> bool:
    """True when the final-report.html sentinel file exists."""
    return (engagement_dir(job_id) / "final-report.html").exists()


def make_error_result(
    job_id: str,
    tenant_id: str,
    error: str,
    duration_s: float = 0.0,
) -> JobResult:
    return JobResult(
        job_id=job_id,
        tenant_id=tenant_id,
        status="failed",
        cost_usd=0.0,
        duration_s=duration_s,
        error=error,
    )
