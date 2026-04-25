"""Tests for the muna-agentsdk ↔ Ares adapter."""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from muna_vaultsdk import VaultRef

from ares_integration.adapter import (
    build_brief,
    is_engagement_complete,
    make_error_result,
    read_engagement_result,
)
from muna_agentsdk import DynamicTarget, IdentityContext, JobSpec, MobileTarget, StaticTarget
from muna_agentsdk._version import SDK_VERSION


def _vault_ref() -> VaultRef:
    return VaultRef(
        path=str(uuid.uuid4()),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )


def _dynamic_spec(tenant_id: str = "tenant-test") -> JobSpec:
    return JobSpec(
        job_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        analysis_type="dynamic",
        target=DynamicTarget(
            base_url="https://staging.acme.com",
            scope="staging.acme.com",
            auth_context=_vault_ref(),
        ),
        requester=IdentityContext(id="lancer-001", type="lancer"),
        sdk_version=SDK_VERSION,
        budget_remaining_usd=50.0,
    )


def _mobile_spec() -> JobSpec:
    return JobSpec(
        job_id=str(uuid.uuid4()),
        tenant_id="tenant-test",
        analysis_type="mobile",
        target=MobileTarget(platform="android", artifact_url="https://argos/artifacts/app.apk"),
        requester=IdentityContext(id="lancer-001", type="lancer"),
        sdk_version=SDK_VERSION,
        budget_remaining_usd=30.0,
    )


# ---------------------------------------------------------------------------
# build_brief
# ---------------------------------------------------------------------------

def test_build_brief_dynamic_contains_target():
    spec = _dynamic_spec()
    brief = build_brief(spec)
    assert "staging.acme.com" in brief
    assert spec.job_id in brief


def test_build_brief_mobile_contains_platform():
    spec = _mobile_spec()
    brief = build_brief(spec)
    assert "ANDROID" in brief
    assert "app.apk" in brief


def test_build_brief_includes_changed_endpoints():
    from muna_agentsdk import DiffContext
    spec = JobSpec(
        job_id=str(uuid.uuid4()), tenant_id="tenant-test",
        analysis_type="dynamic",
        target=DynamicTarget(
            base_url="https://staging.acme.com", scope="staging.acme.com",
            auth_context=_vault_ref(),
        ),
        requester=IdentityContext(id="l", type="lancer"),
        sdk_version=SDK_VERSION,
        budget_remaining_usd=50.0,
        diff=DiffContext(changed_endpoints=("/api/auth", "/api/users")),
    )
    brief = build_brief(spec)
    assert "/api/auth" in brief


def test_build_brief_budget_included():
    spec = _dynamic_spec()
    brief = build_brief(spec)
    assert "50.00" in brief


# ---------------------------------------------------------------------------
# is_engagement_complete
# ---------------------------------------------------------------------------

def test_is_engagement_complete_false_when_no_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ARES_PENTEST_OUTPUT", str(tmp_path))
    assert not is_engagement_complete("job-123")


def test_is_engagement_complete_true_when_report_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("ARES_PENTEST_OUTPUT", str(tmp_path))
    job_id = "job-456"
    (tmp_path / job_id).mkdir()
    (tmp_path / job_id / "final-report.html").write_text("<html>done</html>")
    assert is_engagement_complete(job_id)


# ---------------------------------------------------------------------------
# read_engagement_result — from verification state files
# ---------------------------------------------------------------------------

def test_read_result_from_verification_states(tmp_path, monkeypatch):
    monkeypatch.setenv("ARES_PENTEST_OUTPUT", str(tmp_path))
    spec = _dynamic_spec()
    out = tmp_path / spec.job_id
    verification = out / "verification" / "findings"
    verification.mkdir(parents=True)
    (out / "final-report.html").write_text("<html/>")

    # Write one VERIFIED finding
    state = {
        "finding_id": "f-001",
        "engagement_id": spec.job_id,
        "claim": "SQL injection in /api/login",
        "severity_claimed": "HIGH",
        "final_decision": "VERIFIED",
        "final_decision_reasoning": "Confirmed with PoC",
        "target_url": "https://staging.acme.com/api/login",
        "history": [],
    }
    (verification / "finding-001-state.json").write_text(json.dumps(state))

    result = read_engagement_result(spec, duration_s=3600.0)
    assert result.status == "completed"
    assert result.tenant_id == spec.tenant_id
    assert len(result.findings) == 1
    assert result.findings[0].severity == "high"
    assert result.findings[0].finding_type == "vulnerability"


def test_read_result_rejects_rejected_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("ARES_PENTEST_OUTPUT", str(tmp_path))
    spec = _dynamic_spec()
    out = tmp_path / spec.job_id
    verification = out / "verification" / "findings"
    verification.mkdir(parents=True)
    (out / "final-report.html").write_text("<html/>")

    # REJECTED finding — should not appear in results
    state = {
        "finding_id": "f-fp", "engagement_id": spec.job_id,
        "claim": "False positive XSS",
        "severity_claimed": "LOW",
        "final_decision": "REJECTED",
        "history": [],
    }
    (verification / "finding-fp-state.json").write_text(json.dumps(state))

    result = read_engagement_result(spec, duration_s=60.0)
    assert len(result.findings) == 0


def test_read_result_escalated_becomes_observation(tmp_path, monkeypatch):
    monkeypatch.setenv("ARES_PENTEST_OUTPUT", str(tmp_path))
    spec = _dynamic_spec()
    out = tmp_path / spec.job_id
    verification = out / "verification" / "findings"
    verification.mkdir(parents=True)
    (out / "final-report.html").write_text("<html/>")

    state = {
        "finding_id": "f-esc", "engagement_id": spec.job_id,
        "claim": "Possible SSRF — needs manual verification",
        "severity_claimed": "HIGH",
        "final_decision": "ESCALATED",
        "escalation_reason": "Complex chaining required",
        "history": [],
    }
    (verification / "finding-esc-state.json").write_text(json.dumps(state))

    result = read_engagement_result(spec, duration_s=60.0)
    assert len(result.findings) == 0
    assert result.observations is not None
    assert len(result.observations) == 1
    assert "ESCALATED" in result.observations[0].description


def test_read_result_fallback_to_raw_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("ARES_PENTEST_OUTPUT", str(tmp_path))
    spec = _dynamic_spec()
    out = tmp_path / spec.job_id
    out.mkdir()
    (out / "final-report.html").write_text("<html/>")

    # No verification/ directory — use findings-raw.json fallback
    raw_finding = {
        "id": "f-raw-001",
        "title": "Broken auth",
        "severity": "CRITICAL",
        "description": "No auth check",
        "evidence": "curl -s /admin",
        "endpoint": "/admin",
        "phase": 2,
    }
    with open(out / "findings-raw.json", "w") as f:
        f.write(json.dumps(raw_finding) + "\n")

    result = read_engagement_result(spec, duration_s=100.0)
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


# ---------------------------------------------------------------------------
# make_error_result
# ---------------------------------------------------------------------------

def test_make_error_result():
    result = make_error_result("job-err", "tenant-1", "Hermes crashed")
    assert result.status == "failed"
    assert result.error == "Hermes crashed"
    assert result.tenant_id == "tenant-1"
    assert len(result.findings) == 0
