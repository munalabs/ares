"""Tests for multi-tenancy isolation and scope enforcement.

Épica 10 — multi-tenancy and scope enforcement tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from muna_vaultsdk import VaultRef

from ares_integration.adapter import _scope_firewall_section, build_brief
from ares_integration.engage import app, _jobs
from ares_integration.hermes_trigger import MockTrigger
from muna_agentsdk import DynamicTarget, IdentityContext, JobSpec, MobileTarget, StaticTarget
from muna_agentsdk._version import SDK_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vault_ref() -> VaultRef:
    return VaultRef(
        path=str(uuid.uuid4()),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )


def _dynamic_spec(tenant_id: str = "tenant-a") -> JobSpec:
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
        budget_remaining_usd=20.0,
    )


def _static_spec(tenant_id: str = "tenant-a") -> JobSpec:
    return JobSpec(
        job_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        analysis_type="static",
        target=StaticTarget(
            repo_url="https://github.com/acme/backend",
            commit_sha="abc123def456",
            source_root=".",
            access_token=_vault_ref(),
        ),
        requester=IdentityContext(id="lancer-001", type="lancer"),
        sdk_version=SDK_VERSION,
        budget_remaining_usd=20.0,
    )


async def _noop_watcher(job_id: str, *args, **kwargs) -> None:
    """No-op watcher for tests — prevents asyncio.sleep from hanging."""


@pytest.fixture(autouse=True)
def setup_app(tmp_path, monkeypatch):
    app.state.trigger = MockTrigger(output_dir=str(tmp_path))
    monkeypatch.setattr("ares_integration.engage._watch_engagement", _noop_watcher)
    _jobs.clear()
    yield
    _jobs.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Multi-tenancy isolation — status endpoint
# ---------------------------------------------------------------------------

async def test_status_returns_404_for_wrong_tenant(client):
    """GET /engage/{job_id}/status with X-Tenant-Id that doesn't match returns 404."""
    spec_a = _dynamic_spec(tenant_id="tenant-a")
    await client.post("/engage", json=spec_a.to_dict())

    resp = await client.get(
        f"/engage/{spec_a.job_id}/status",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert resp.status_code == 404


async def test_status_returns_200_for_correct_tenant(client):
    """GET /engage/{job_id}/status with matching X-Tenant-Id returns 200."""
    spec_a = _dynamic_spec(tenant_id="tenant-a")
    await client.post("/engage", json=spec_a.to_dict())

    resp = await client.get(
        f"/engage/{spec_a.job_id}/status",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert resp.status_code == 200


async def test_status_returns_200_without_tenant_header(client):
    """Status endpoint without header still works (header is optional)."""
    spec = _dynamic_spec(tenant_id="tenant-a")
    await client.post("/engage", json=spec.to_dict())

    resp = await client.get(f"/engage/{spec.job_id}/status")
    assert resp.status_code == 200


async def test_tenant_a_cannot_access_tenant_b_status(client):
    """Tenant B cannot read the status of a job started by tenant A."""
    spec_a = _dynamic_spec(tenant_id="tenant-a")
    spec_b = _dynamic_spec(tenant_id="tenant-b")

    await client.post("/engage", json=spec_a.to_dict())
    await client.post("/engage", json=spec_b.to_dict())

    # Tenant B tries to access tenant A's job
    resp = await client.get(
        f"/engage/{spec_a.job_id}/status",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert resp.status_code == 404

    # Tenant A can access their own job
    resp = await client.get(
        f"/engage/{spec_a.job_id}/status",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Multi-tenancy isolation — result endpoint
# ---------------------------------------------------------------------------

async def test_result_returns_404_for_wrong_tenant(client):
    """GET /engage/{job_id}/result with mismatched X-Tenant-Id returns 404."""
    spec_a = _dynamic_spec(tenant_id="tenant-a")
    await client.post("/engage", json=spec_a.to_dict())

    # Mark as completed so the tenant check is the only thing blocking
    from muna_agentsdk import JobResult
    result = JobResult(
        job_id=spec_a.job_id, tenant_id=spec_a.tenant_id,
        status="completed", cost_usd=1.0, duration_s=60.0,
    )
    _jobs[spec_a.job_id]["status"] = "completed"
    _jobs[spec_a.job_id]["result"] = result

    resp = await client.get(
        f"/engage/{spec_a.job_id}/result",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert resp.status_code == 404


async def test_result_returns_200_for_correct_tenant(client):
    """GET /engage/{job_id}/result with matching X-Tenant-Id returns 200."""
    spec_a = _dynamic_spec(tenant_id="tenant-a")
    await client.post("/engage", json=spec_a.to_dict())

    from muna_agentsdk import JobResult
    result = JobResult(
        job_id=spec_a.job_id, tenant_id=spec_a.tenant_id,
        status="completed", cost_usd=1.0, duration_s=60.0,
    )
    _jobs[spec_a.job_id]["status"] = "completed"
    _jobs[spec_a.job_id]["result"] = result

    resp = await client.get(
        f"/engage/{spec_a.job_id}/result",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Known gap: status endpoint silently allows access without tenant header
#
# The header check is optional — a caller that omits X-Tenant-Id gets access
# to any job_id they can enumerate. For this internal service (called by
# nats_consumer, not external users) this is acceptable for now, but it
# should be hardened once nats_consumer passes tenant_id on every poll.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason=(
        "TODO: enforce X-Tenant-Id as required on server side. "
        "nats_consumer now passes the header (fixed 2026-04-27), but engage.py still "
        "accepts headerless requests. Requires architectural decision: make header "
        "mandatory (breaks any caller without it) or rely on network isolation "
        "(this is an internal service — only nats_consumer and nats_consumer-like "
        "callers reach it)."
    )
)
async def test_status_endpoint_missing_tenant_check(client):
    """Without X-Tenant-Id header, any job_id is accessible — known gap.

    The current implementation allows access to any job when no header is
    provided. nats_consumer now passes the header. Server-side enforcement
    is a future hardening step. This xfail documents the remaining gap.
    """
    spec = _dynamic_spec(tenant_id="tenant-a")
    await client.post("/engage", json=spec.to_dict())

    # No header — this should ideally return 401/403, but currently returns 200.
    resp = await client.get(f"/engage/{spec.job_id}/status")
    # We assert 401 to document what the hardened behavior should be.
    # This will xfail (currently returns 200, not 401).
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scope enforcement — _scope_firewall_section unit tests
# ---------------------------------------------------------------------------

def test_scope_firewall_blocks_aws_metadata():
    section = _scope_firewall_section("staging.acme.com")
    assert "169.254.169.254" in section or "169.254.0.0/16" in section


def test_scope_firewall_blocks_private_ranges():
    section = _scope_firewall_section("staging.acme.com")
    assert "10.0.0.0/8" in section
    assert "172.16.0.0/12" in section
    assert "192.168.0.0/16" in section
    assert "127.0.0.1" in section


def test_scope_firewall_includes_scope_target():
    section = _scope_firewall_section("*.acme.com")
    assert "*.acme.com" in section or "SCOPE" in section.upper()


# ---------------------------------------------------------------------------
# Scope enforcement — build_brief integration
# ---------------------------------------------------------------------------

def test_brief_injection_resistance_on_dynamic_target():
    spec = _dynamic_spec()
    brief = build_brief(spec)
    assert "PROMPT INJECTION" in brief.upper() or "adversarial_server_response" in brief


def test_brief_injection_resistance_on_static_target():
    spec = _static_spec()
    brief = build_brief(spec)
    assert "PROMPT INJECTION" in brief.upper() or "adversarial_server_response" in brief


def test_scope_firewall_not_present_for_static_target():
    """StaticTarget briefs should not contain the SCOPE FIREWALL section."""
    spec = _static_spec()
    brief = build_brief(spec)
    assert "SCOPE FIREWALL" not in brief


def test_scope_firewall_present_for_dynamic_target():
    """DynamicTarget briefs must contain the SCOPE FIREWALL section."""
    spec = _dynamic_spec()
    brief = build_brief(spec)
    assert "SCOPE FIREWALL" in brief
