"""NATS consumer — bridges jobs.dynamic.pending / jobs.mobile.pending to /engage HTTP.

Receives a muna-agentsdk JobSpec from NATS, calls the local HTTP adapter,
publishes the result to jobs.results, and sends heartbeats to jobs.heartbeat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime

import httpx
import nats
from muna_agentsdk import Heartbeat, JobResult

logger = logging.getLogger(__name__)

SUBJECT_DYNAMIC = "jobs.dynamic.pending"
SUBJECT_MOBILE = "jobs.mobile.pending"
SUBJECT_RESULTS = "jobs.results"
SUBJECT_HEARTBEAT = "jobs.heartbeat"
HEARTBEAT_INTERVAL_S = 30
POLL_INTERVAL_S = 30
MAX_WAIT_S = 21600  # 6h


class ConsumerConfig:
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    engage_url: str = os.getenv("ARES_ENGAGE_URL", "http://localhost:8001")
    worker_id: str = os.getenv("ARES_WORKER_ID", "ares-worker-1")

    def __init__(self) -> None:
        self.nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
        self.engage_url = os.getenv("ARES_ENGAGE_URL", "http://localhost:8001")
        self.worker_id = os.getenv("ARES_WORKER_ID", "ares-worker-1")


async def run_consumer(config: ConsumerConfig | None = None) -> None:
    cfg = config or ConsumerConfig()
    nc = await nats.connect(cfg.nats_url)
    logger.info(f"Ares NATS consumer connected to {cfg.nats_url}")

    async def on_job(msg: object) -> None:
        try:
            body = json.loads(msg.data)  # type: ignore[attr-defined]
            job_id = body.get("job_id", "unknown")
            tenant_id = body.get("tenant_id", "")
        except Exception as e:
            logger.error(f"Failed to parse job: {e}")
            return

        logger.info(f"Received job: {job_id} tenant={tenant_id}")
        result = await _process_job(body, cfg, nc)
        await nc.publish(SUBJECT_RESULTS, json.dumps(result.to_dict()).encode())
        logger.info(f"Result published: {job_id} status={result.status}")

    await nc.subscribe(SUBJECT_DYNAMIC, cb=on_job)
    await nc.subscribe(SUBJECT_MOBILE, cb=on_job)
    logger.info(f"Subscribed to {SUBJECT_DYNAMIC} and {SUBJECT_MOBILE}")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await nc.close()


async def _process_job(body: dict, cfg: ConsumerConfig, nc: object) -> JobResult:
    job_id = body.get("job_id", "unknown")
    tenant_id = body.get("tenant_id", "")
    t0 = time.monotonic()

    async with httpx.AsyncClient(base_url=cfg.engage_url, timeout=30) as client:
        # Start engagement
        try:
            resp = await client.post("/engage", json=body)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to start engagement {job_id}: {e}")
            return _error_result(job_id, tenant_id, str(e))

        # Poll for completion with heartbeats
        deadline = time.monotonic() + MAX_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)

            # Publish heartbeat
            hb = Heartbeat(
                job_id=job_id,
                tenant_id=tenant_id,
                worker_id=cfg.worker_id,
                timestamp=datetime.now(tz=UTC),
                cost_usd_accumulated=0.0,  # Ares doesn't track cost mid-run
            )
            try:
                await nc.publish(SUBJECT_HEARTBEAT, json.dumps(hb.to_dict()).encode())  # type: ignore[attr-defined]
            except Exception:
                pass

            # Check status
            try:
                status_resp = await client.get(f"/engage/{job_id}/status")
                status_data = status_resp.json()
                status = status_data.get("status", "running")
            except Exception:
                continue

            if status == "completed":
                try:
                    result_resp = await client.get(f"/engage/{job_id}/result")
                    return JobResult.from_dict(result_resp.json())
                except Exception as e:
                    return _error_result(job_id, tenant_id, f"Result fetch failed: {e}")

            if status == "failed":
                return _error_result(
                    job_id, tenant_id,
                    status_data.get("error", "Engagement failed"),
                    duration_s=time.monotonic() - t0,
                )

    return _error_result(job_id, tenant_id, "timeout", duration_s=time.monotonic() - t0)


def _error_result(
    job_id: str, tenant_id: str, error: str, duration_s: float = 0.0
) -> JobResult:
    return JobResult(
        job_id=job_id,
        tenant_id=tenant_id,
        status="failed",
        cost_usd=0.0,
        duration_s=duration_s,
        error=error,
    )
