"""Tests for the FastAPI /engage HTTP endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from muna_vaultsdk import VaultRef

from ares_integration.engage import app, _jobs
from ares_integration.hermes_trigger import MockTrigger
from muna_agentsdk import DynamicTarget, IdentityContext, JobSpec
from muna_agentsdk._version import SDK_VERSION


def _vault_ref() -> VaultRef:
    return VaultRef(
        path=str(uuid.uuid4()),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )


def _spec(tenant_id: str = "tenant-t") -> JobSpec:
    return JobSpec(
        job_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        analysis_type="dynamic",
        target=DynamicTarget(
            base_url="https://staging.example.com",
            scope="staging.example.com",
            auth_context=_vault_ref(),
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
    # Prevent the background watcher from blocking the test event loop
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
# POST /engage
# ---------------------------------------------------------------------------

async def test_start_engagement_returns_202(client):
    spec = _spec()
    resp = await client.post("/engage", json=spec.to_dict())
    assert resp.status_code == 202
    assert resp.json()["job_id"] == spec.job_id


async def test_start_engagement_job_in_state(client):
    spec = _spec()
    await client.post("/engage", json=spec.to_dict())
    assert spec.job_id in _jobs
    assert _jobs[spec.job_id]["status"] in ("running", "queued")


async def test_start_engagement_invalid_spec(client):
    resp = await client.post("/engage", json={"bad": "data"})
    assert resp.status_code == 422


async def test_start_engagement_trigger_fails(client, tmp_path):
    """If the trigger raises, 503 is returned."""
    class FailingTrigger:
        def start(self, engagement_id: str, brief: str) -> None:
            raise RuntimeError("Hermes not found")

    app.state.trigger = FailingTrigger()
    spec = _spec()
    resp = await client.post("/engage", json=spec.to_dict())
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /engage/{id}/status
# ---------------------------------------------------------------------------

async def test_get_status_queued(client):
    spec = _spec()
    await client.post("/engage", json=spec.to_dict())
    resp = await client.get(f"/engage/{spec.job_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("running", "queued")
    assert resp.json()["timeout_s"] == 21600


async def test_get_status_not_found(client):
    resp = await client.get("/engage/nonexistent/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /engage/{id}/result
# ---------------------------------------------------------------------------

async def test_get_result_not_ready(client):
    spec = _spec()
    await client.post("/engage", json=spec.to_dict())
    resp = await client.get(f"/engage/{spec.job_id}/result")
    assert resp.status_code == 404
    assert "not available" in resp.json()["detail"]


async def test_get_result_completed(client, tmp_path):
    """Manually inject a completed result and verify the endpoint."""
    spec = _spec()
    await client.post("/engage", json=spec.to_dict())

    from muna_agentsdk import JobResult
    result = JobResult(
        job_id=spec.job_id, tenant_id=spec.tenant_id,
        status="completed", cost_usd=5.0, duration_s=3600.0,
    )
    _jobs[spec.job_id]["status"] = "completed"
    _jobs[spec.job_id]["result"] = result

    resp = await client.get(f"/engage/{spec.job_id}/result")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["tenant_id"] == spec.tenant_id


async def test_get_result_failed(client):
    spec = _spec()
    await client.post("/engage", json=spec.to_dict())
    _jobs[spec.job_id]["status"] = "failed"
    _jobs[spec.job_id]["error"] = "Hermes crashed"

    resp = await client.get(f"/engage/{spec.job_id}/result")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert "Hermes crashed" in data["error"]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
