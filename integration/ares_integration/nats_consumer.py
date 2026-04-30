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
        # Per-engagement timeout — how long the consumer waits before declaring timeout.
        # Default 6h; configurable via ARES_ENGAGEMENT_TIMEOUT_S.
        self.engagement_timeout_s: int = int(os.getenv("ARES_ENGAGEMENT_TIMEOUT_S", str(MAX_WAIT_S)))
        # Argos Knowledge Base — best-effort, never fails a job
        self.argos_url: str = os.getenv("ARGOS_URL", "")
        self.argos_token: str = os.getenv("ARGOS_TOKEN", "")


async def _pull_subscribe_with_retry(
    js: object,
    subject: str,
    durable: str,
    consumer_config: object,
) -> object:
    """Subscribe with retry loop until the stream exists (up to 1 minute)."""
    import nats.js.errors

    for attempt in range(12):
        try:
            sub = await js.pull_subscribe(  # type: ignore[attr-defined]
                subject,
                durable=durable,
                config=consumer_config,
            )
            logger.info(f"Subscribed via JetStream: subject={subject} durable={durable}")
            return sub
        except nats.js.errors.NotFoundError:
            if attempt < 11:
                logger.warning(
                    f"JetStream stream not found for {subject} "
                    f"(attempt {attempt + 1}/12) — waiting 5s..."
                )
                await asyncio.sleep(5)
            else:
                raise RuntimeError(
                    f"JetStream stream not found for {subject} after 1 minute. "
                    "Restart worker once Argos is up."
                )
    # unreachable, but satisfies type checker
    raise RuntimeError("subscribe retry loop exhausted")  # pragma: no cover


async def run_consumer(config: ConsumerConfig | None = None) -> None:
    from nats.js.api import AckPolicy, ConsumerConfig as JsConsumerConfig, DeliverPolicy

    cfg = config or ConsumerConfig()
    nc = await nats.connect(cfg.nats_url)
    logger.info(f"Ares NATS consumer connected to {cfg.nats_url}")

    js = nc.jetstream()

    consumer_cfg = JsConsumerConfig(
        max_deliver=3,
        ack_wait=7200,  # 2h — must exceed max job duration
        backoff=[30, 120, 600],
        ack_policy=AckPolicy.EXPLICIT,
        deliver_policy=DeliverPolicy.ALL,
    )

    try:
        sub_dynamic = await _pull_subscribe_with_retry(
            js,
            SUBJECT_DYNAMIC,
            durable=f"ares-worker-dynamic-{cfg.worker_id}",
            consumer_config=consumer_cfg,
        )
        sub_mobile = await _pull_subscribe_with_retry(
            js,
            SUBJECT_MOBILE,
            durable=f"ares-worker-mobile-{cfg.worker_id}",
            consumer_config=consumer_cfg,
        )
    except RuntimeError as e:
        logger.error(str(e))
        await nc.close()
        return

    logger.info(f"Worker {cfg.worker_id} polling {SUBJECT_DYNAMIC} and {SUBJECT_MOBILE}")

    async def _handle_msg(msg: object) -> None:
        try:
            body = json.loads(msg.data)  # type: ignore[attr-defined]
            job_id = body.get("job_id", "unknown")
            tenant_id = body.get("tenant_id", "")
        except Exception as e:
            logger.error(f"Failed to parse job: {e}")
            await msg.ack()  # type: ignore[attr-defined]
            return

        # Ack before processing: job can run up to 6h; ack_wait=2h would expire.
        await msg.ack()  # type: ignore[attr-defined]
        logger.info(f"Received job: {job_id} tenant={tenant_id}")
        result = await _process_job(body, cfg, nc)
        await nc.publish(SUBJECT_RESULTS, json.dumps(result.to_dict()).encode())
        logger.info(f"Result published: {job_id} status={result.status}")

    try:
        while True:
            for sub in (sub_dynamic, sub_mobile):
                try:
                    msgs = await sub.fetch(batch=1, timeout=5)  # type: ignore[attr-defined]
                except nats.errors.TimeoutError:
                    continue
                except Exception as e:
                    logger.warning(f"fetch error: {e}")
                    await asyncio.sleep(1)
                    continue

                for msg in msgs:
                    await _handle_msg(msg)
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
        deadline = time.monotonic() + cfg.engagement_timeout_s
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

            # Check status — pass tenant header so the endpoint can validate ownership
            tenant_headers = {"X-Tenant-Id": tenant_id} if tenant_id else {}
            try:
                status_resp = await client.get(
                    f"/engage/{job_id}/status", headers=tenant_headers
                )
                status_data = status_resp.json()
                status = status_data.get("status", "running")
            except Exception:
                continue

            if status == "completed":
                try:
                    result_resp = await client.get(
                        f"/engage/{job_id}/result", headers=tenant_headers
                    )
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
