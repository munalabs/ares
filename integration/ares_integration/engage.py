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

from fastapi import BackgroundTasks, FastAPI, HTTPException

from muna_agentsdk import JobResult, JobSpec, JobStatus

from ares_integration.adapter import (
    build_brief,
    engagement_dir,
    is_engagement_complete,
    make_error_result,
    read_engagement_result,
)
from ares_integration.hermes_trigger import HermesTrigger, default_trigger

logger = logging.getLogger(__name__)

# In-memory job state (stateless across restarts — acceptable for single-node)
_jobs: dict[str, dict[str, Any]] = {}

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
    _jobs[job_id] = {
        "status": "queued",
        "tenant_id": spec.tenant_id,
        "started_at": time.monotonic(),
        "spec": spec,
    }

    brief = build_brief(spec)
    trigger = _get_trigger()

    try:
        trigger.start(job_id, brief)
        _jobs[job_id]["status"] = "running"
        logger.info(f"Engagement started: {job_id}")
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)
        raise HTTPException(status_code=503, detail=f"Failed to start Hermes: {e}")

    # Background watcher: polls for completion and caches the result
    poll_interval = getattr(app.state, "poll_interval_s", 30)
    background_tasks.add_task(_watch_engagement, job_id, poll_interval)

    return {"job_id": job_id}


async def _watch_engagement(job_id: str, poll_interval_s: int = 30, max_wait_s: int = 21600) -> None:
    """Poll for final-report.html and cache the result when ready."""
    deadline = time.monotonic() + max_wait_s
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
                except Exception as e:
                    _jobs[job_id]["status"] = "failed"
                    _jobs[job_id]["error"] = f"Result parsing failed: {e}"
            return

    _jobs[job_id]["status"] = "failed"
    _jobs[job_id]["error"] = "timeout"
    logger.warning(f"Engagement timed out: {job_id}")


# ---------------------------------------------------------------------------
# GET /engage/{job_id}/status
# ---------------------------------------------------------------------------

@app.get("/engage/{job_id}/status")
async def get_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
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
async def get_result(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
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
