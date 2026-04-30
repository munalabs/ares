"""FastAPI service: HTTP trigger and status polling for Ares engagements.

POST /engage            — start an engagement from a JobSpec
GET  /engage/{id}/status — poll status
GET  /engage/{id}/result — fetch completed JobResult
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException

from muna_agentsdk import JobResult, JobSpec, JobStatus

from ares_integration.adapter import (
    build_brief,
    engagement_dir,
    is_engagement_complete,
    make_error_result,
    read_engagement_result,
)
from ares_integration.docker_network import create_engagement_network, remove_engagement_network
from ares_integration.hermes_trigger import HermesTrigger, default_trigger
from ares_integration.kb import KBClient

logger = logging.getLogger(__name__)

# In-memory job state (stateless across restarts — acceptable for single-node)
_jobs: dict[str, dict[str, Any]] = {}

# Concurrency limit — max parallel engagements (configurable per plan)
import os as _os
_MAX_PARALLEL_ENGAGEMENTS = int(_os.getenv("ARES_MAX_PARALLEL_ENGAGEMENTS", "3"))
# Completed/failed jobs are evicted after this many seconds to prevent unbounded growth
_JOB_TTL_S = int(_os.getenv("ARES_JOB_TTL_S", "7200"))  # 2h


def _evict_stale_jobs() -> None:
    """Remove completed/failed jobs older than _JOB_TTL_S. Call before any _jobs mutation."""
    cutoff = time.monotonic() - _JOB_TTL_S
    stale = [
        jid for jid, j in _jobs.items()
        if j.get("status") in ("completed", "failed", "cancelled")
        and j.get("started_at", 0) < cutoff  # default 0 → always stale (never suppress)
    ]
    for jid in stale:
        _jobs.pop(jid, None)
    if stale:
        logger.debug(f"Evicted {len(stale)} stale jobs from in-memory store")

app = FastAPI(
    title="Ares Integration Adapter",
    version="1.0.0",
    description="HTTP interface between Argos and the Ares Hermes pentest agent",
)


def _get_trigger() -> HermesTrigger:
    return app.state.trigger if hasattr(app.state, "trigger") else default_trigger()


# ---------------------------------------------------------------------------
# POST /engage
# ---------------------------------------------------------------------------

def _get_kb(tenant_id: str) -> KBClient | None:
    argos_url = getattr(app.state, "argos_url", "")
    argos_token = getattr(app.state, "argos_token", "")
    if argos_url and argos_token:
        return KBClient(argos_url=argos_url, token=argos_token, tenant_id=tenant_id)
    return None


@app.post("/engage", status_code=202)
async def start_engagement(
    request_body: dict,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        spec = JobSpec.from_dict(request_body)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid JobSpec: {e}")

    job_id = spec.job_id
    _evict_stale_jobs()

    # Enforce concurrency limit
    active = sum(1 for j in _jobs.values() if j.get("status") == "running")
    if active >= _MAX_PARALLEL_ENGAGEMENTS:
        raise HTTPException(
            status_code=429,
            detail=f"Concurrency limit reached ({active}/{_MAX_PARALLEL_ENGAGEMENTS} engagements running). Retry later.",
        )

    # Route MobileTarget to the dedicated mobile analysis pipeline
    from muna_agentsdk import DynamicTarget, MobileTarget
    if isinstance(spec.target, MobileTarget):
        return await _start_mobile_engagement(spec, background_tasks)

    # KB: fetch previous surface to enrich the brief (best-effort)
    prev_surface: dict | None = None
    if isinstance(spec.target, DynamicTarget):
        kb = _get_kb(spec.tenant_id)
        if kb:
            prev_surface = await kb.fetch_surface(str(spec.target.base_url))

    _jobs[job_id] = {
        "status": "queued",
        "tenant_id": spec.tenant_id,
        "started_at": time.monotonic(),
        "spec": spec,
        "prev_surface": prev_surface,
    }

    brief = build_brief(spec, prev_surface=prev_surface)
    trigger = _get_trigger()

    # Create per-engagement Docker network (no-op unless ARES_DOCKER_NETWORK_ISOLATION=true)
    network_name = await create_engagement_network(job_id)
    if network_name:
        _jobs[job_id]["network_name"] = network_name

    try:
        trigger.start(job_id, brief)
        _jobs[job_id]["status"] = "running"
        logger.info(f"Engagement started: {job_id}")
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)
        if network_name:
            await remove_engagement_network(network_name)
        raise HTTPException(status_code=503, detail=f"Failed to start Hermes: {e}")

    # Background watcher: polls for completion and caches the result
    poll_interval = getattr(app.state, "poll_interval_s", 30)
    background_tasks.add_task(_watch_engagement, job_id, poll_interval)

    return {"job_id": job_id}


async def _start_mobile_engagement(spec, background_tasks: BackgroundTasks) -> dict:
    """Route MobileTarget to the MobileAnalyzer pipeline (MoBSF + ADB + Frida)."""
    from ares_integration.mobile import MobileAnalyzer

    job_id = spec.job_id
    _jobs[job_id] = {
        "status": "running",
        "tenant_id": spec.tenant_id,
        "started_at": time.monotonic(),
        "spec": spec,
    }
    logger.info(f"Mobile engagement started: {job_id} platform={spec.target.platform}")

    async def _run():
        try:
            analyzer = MobileAnalyzer(spec)
            result = await analyzer.run()
            _jobs[job_id]["result"] = result
            _jobs[job_id]["status"] = result.status
            logger.info(f"Mobile engagement complete: {job_id} status={result.status}")
        except Exception as e:
            logger.exception(f"Mobile engagement failed: {job_id}")
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)

    background_tasks.add_task(_run)
    return {"job_id": job_id}


async def _watch_engagement(job_id: str, poll_interval_s: int = 30, max_wait_s: int = 21600) -> None:
    """Poll for final-report.html and cache the result when ready."""
    deadline = time.monotonic() + max_wait_s
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_s)
            if is_engagement_complete(job_id):
                job = _jobs.get(job_id, {})
                duration = time.monotonic() - job.get("started_at", time.monotonic())
                spec = job.get("spec")
                if spec:
                    try:
                        result = read_engagement_result(spec, duration_s=duration)
                        _jobs[job_id]["result"] = result
                        _jobs[job_id]["status"] = "completed"
                        logger.info(f"Engagement complete: {job_id}")
                        # KB: push discovered surface (best-effort)
                        from muna_agentsdk import DynamicTarget
                        if isinstance(spec.target, DynamicTarget):
                            kb = _get_kb(spec.tenant_id)
                            if kb:
                                await kb.push_surface(
                                    str(spec.target.base_url),
                                    _build_surface_data(spec, result),
                                )
                    except Exception as e:
                        _jobs[job_id]["status"] = "failed"
                        _jobs[job_id]["error"] = f"Result parsing failed: {e}"
                return

        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = "timeout"
        logger.warning(f"Engagement timed out: {job_id}")
    finally:
        # Clean up Docker network (best-effort, no-op if isolation is disabled)
        network_name = _jobs.get(job_id, {}).get("network_name")
        if network_name:
            await remove_engagement_network(network_name)


# ---------------------------------------------------------------------------
# GET /engage/{job_id}/status
# ---------------------------------------------------------------------------

@app.get("/engage/{job_id}/status")
async def get_status(job_id: str, x_tenant_id: str | None = Header(None)) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Engagement not found")
    # Validate tenant ownership when header is provided.
    # Internal callers (nats_consumer) always pass X-Tenant-Id.
    # External callers without the header see any job — acceptable for this
    # internal-only service where network isolation provides the outer boundary.
    if x_tenant_id and job.get("tenant_id") != x_tenant_id:
        raise HTTPException(status_code=404, detail="Engagement not found")

    status = job["status"]
    return JobStatus(
        job_id=job_id,
        status=status,  # type: ignore[arg-type]
        timeout_s=21600,  # 6h default for dynamic engagements
        progress=1.0 if status == "completed" else None,
    ).to_dict()


# ---------------------------------------------------------------------------
# GET /engage/{job_id}/result
# ---------------------------------------------------------------------------

@app.get("/engage/{job_id}/result")
async def get_result(job_id: str, x_tenant_id: str | None = Header(None)) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Engagement not found")
    if x_tenant_id and job.get("tenant_id") != x_tenant_id:
        raise HTTPException(status_code=404, detail="Engagement not found")

    if job["status"] not in ("completed", "failed"):
        raise HTTPException(
            status_code=404,
            detail=f"Result not available yet (status={job['status']})",
        )

    result: JobResult = job.get("result") or make_error_result(
        job_id, job.get("tenant_id", ""), job.get("error", "unknown error")
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_surface_data(spec: JobSpec, result: JobResult) -> dict:
    """Summarise the engagement result as surface data for the KB."""
    from datetime import UTC, datetime
    from muna_agentsdk import DynamicTarget
    target = spec.target
    return {
        "target_url": str(target.base_url) if isinstance(target, DynamicTarget) else "",
        "scope": getattr(target, "scope", ""),
        "findings": [
            {
                "id": f.id,
                "finding_type": f.finding_type,
                "severity": f.severity,
                "title": f.title,
            }
            for f in result.findings
        ],
        "observations": [
            {"type": o.observation_type, "description": o.description}
            for o in result.observations
        ],
        "cost_usd": result.cost_usd,
        "duration_s": result.duration_s,
        "tested_at": datetime.now(tz=UTC).isoformat(),
    }
