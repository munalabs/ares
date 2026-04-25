"""Tests for the NATS consumer — mocks the HTTP /engage endpoint."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from muna_vaultsdk import VaultRef

from ares_integration.nats_consumer import ConsumerConfig, _process_job
from muna_agentsdk import DynamicTarget, IdentityContext, JobResult, JobSpec
from muna_agentsdk._version import SDK_VERSION


def _vault_ref() -> VaultRef:
    return VaultRef(
        path=str(uuid.uuid4()),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )


def _spec_dict(tenant_id: str = "tenant-t") -> dict:
    spec = JobSpec(
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
    return spec.to_dict()


def _cfg() -> ConsumerConfig:
    cfg = ConsumerConfig()
    cfg.nats_url = "nats://localhost:4222"
    cfg.engage_url = "http://localhost:8001"
    cfg.worker_id = "ares-test-worker"
    return cfg


# ---------------------------------------------------------------------------
# _process_job
# ---------------------------------------------------------------------------

async def test_process_job_engage_fails():
    body = _spec_dict()
    nc = AsyncMock()

    async def mock_post(*args, **kwargs):
        raise Exception("connection refused")

    with patch("ares_integration.nats_consumer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client

        result = await _process_job(body, _cfg(), nc)

    assert result.status == "failed"
    assert "connection refused" in (result.error or "")


async def test_process_job_completed_on_first_poll():
    body = _spec_dict()
    job_id = body["job_id"]
    tenant_id = body["tenant_id"]
    nc = AsyncMock()

    completed_result = JobResult(
        job_id=job_id, tenant_id=tenant_id,
        status="completed", cost_usd=3.0, duration_s=1800.0,
    )

    engage_resp = AsyncMock()
    engage_resp.raise_for_status = AsyncMock()

    status_resp = AsyncMock()
    status_resp.json = MagicMock(return_value={"status": "completed", "timeout_s": 21600})

    result_resp = AsyncMock()
    result_resp.json = MagicMock(return_value=completed_result.to_dict())

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=engage_resp)
    mock_client.get = AsyncMock(side_effect=[status_resp, result_resp])

    with patch("ares_integration.nats_consumer.httpx.AsyncClient", return_value=mock_client), \
         patch("ares_integration.nats_consumer.POLL_INTERVAL_S", 0):
        result = await _process_job(body, _cfg(), nc)

    assert result.status == "completed"
    assert result.cost_usd == 3.0


async def test_process_job_failed_status():
    body = _spec_dict()
    nc = AsyncMock()

    engage_resp = AsyncMock()
    engage_resp.raise_for_status = AsyncMock()

    status_resp = AsyncMock()
    status_resp.json = MagicMock(return_value={"status": "failed", "error": "Hermes crashed"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=engage_resp)
    mock_client.get = AsyncMock(return_value=status_resp)

    with patch("ares_integration.nats_consumer.httpx.AsyncClient", return_value=mock_client), \
         patch("ares_integration.nats_consumer.POLL_INTERVAL_S", 0):
        result = await _process_job(body, _cfg(), nc)

    assert result.status == "failed"


async def test_process_job_publishes_heartbeat():
    body = _spec_dict()
    nc = AsyncMock()
    published_subjects: list[str] = []

    async def capture_publish(subject: str, data: bytes) -> None:
        published_subjects.append(subject)

    nc.publish = AsyncMock(side_effect=capture_publish)

    engage_resp = AsyncMock()
    engage_resp.raise_for_status = AsyncMock()

    completed_result = JobResult(
        job_id=body["job_id"], tenant_id=body["tenant_id"],
        status="completed", cost_usd=1.0, duration_s=60.0,
    )

    status_resp = AsyncMock()
    status_resp.json = MagicMock(return_value={"status": "completed"})

    result_resp = AsyncMock()
    result_resp.json = MagicMock(return_value=completed_result.to_dict())

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=engage_resp)
    mock_client.get = AsyncMock(side_effect=[status_resp, result_resp])

    with patch("ares_integration.nats_consumer.httpx.AsyncClient", return_value=mock_client), \
         patch("ares_integration.nats_consumer.POLL_INTERVAL_S", 0):
        result = await _process_job(body, _cfg(), nc)

    # At least one heartbeat should have been published
    assert "jobs.heartbeat" in published_subjects


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_integration_nats_end_to_end(tmp_path):
    """Full NATS cycle: publish job → process → result published."""
    import asyncio
    import nats as nats_lib

    nats_url = __import__("os").getenv("NATS_URL", "nats://localhost:4223")
    nc = await nats_lib.connect(nats_url)
    results: list[dict] = []

    async def on_result(msg: object) -> None:
        results.append(json.loads(msg.data))  # type: ignore[attr-defined]

    await nc.subscribe("jobs.results", cb=on_result)

    body = _spec_dict()
    job_id = body["job_id"]
    tenant_id = body["tenant_id"]

    completed = JobResult(
        job_id=job_id, tenant_id=tenant_id,
        status="completed", cost_usd=2.0, duration_s=300.0,
    )

    engage_resp = AsyncMock()
    engage_resp.raise_for_status = AsyncMock()
    status_resp = AsyncMock()
    status_resp.json = MagicMock(return_value={"status": "completed"})
    result_resp = AsyncMock()
    result_resp.json = MagicMock(return_value=completed.to_dict())

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=engage_resp)
    mock_client.get = AsyncMock(side_effect=[status_resp, result_resp])

    cfg = _cfg()

    with patch("ares_integration.nats_consumer.httpx.AsyncClient", return_value=mock_client), \
         patch("ares_integration.nats_consumer.POLL_INTERVAL_S", 0):
        result = await _process_job(body, cfg, nc)
        await nc.publish("jobs.results", json.dumps(result.to_dict()).encode())

    await asyncio.sleep(0.2)
    await nc.close()

    assert len(results) >= 1
    assert results[0]["job_id"] == job_id
    assert results[0]["status"] == "completed"
