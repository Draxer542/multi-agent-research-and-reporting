"""
FastAPI application — Research Agent trigger endpoint.

Provides three routes:

- ``POST /research``              — submit a new research job
- ``GET  /research/{id}``         — poll job status
- ``POST /replay/{id}``           — replay a failed / dead-lettered job
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from api.models import ResearchRequest, ResearchAccepted
from api.queue_client import enqueue_job
from tools.injection_filter import sanitize_prompt
from tools.scope_guard import check_scope
from core.logging import get_logger
from monitoring.telemetry import setup_telemetry, instrument_app

logger = get_logger(__name__, component="api")

# Background worker task handle (prevents GC and allows graceful shutdown)
_worker_task: asyncio.Task | None = None

# ── Application ──────────────────────────────────────────────────────
app = FastAPI(
    title="Research Agent API",
    description="Multi-agent research and reporting pipeline trigger.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse("static/index.html")


@app.on_event("startup")
async def _startup() -> None:
    """Initialise telemetry, instrument FastAPI, and start the queue worker."""
    global _worker_task
    setup_telemetry()
    instrument_app(app)

    # Auto-start the queue worker as a background task
    from worker.queue_worker import run_worker
    _worker_task = asyncio.create_task(run_worker())
    logger.info("Research Agent API started (worker auto-started)")


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Gracefully cancel the background worker on server shutdown."""
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Background worker stopped")


# ── Routes ───────────────────────────────────────────────────────────


@app.post(
    "/research",
    response_model=ResearchAccepted,
    status_code=202,
    summary="Submit a new research job",
)
async def create_research_job(request: ResearchRequest) -> ResearchAccepted:
    """
    Validate the prompt, assign a correlation ID, and enqueue the job
    to Azure Queue Storage for asynchronous processing.
    """
    correlation_id = str(uuid.uuid4())
    ctx = logger.bind(correlation_id=correlation_id)

    # ── Prompt safety check ──────────────────────────────────────
    safe_prompt = sanitize_prompt(request.prompt)
    if safe_prompt is None:
        ctx.warning("Prompt rejected by injection filter")
        raise HTTPException(
            status_code=400,
            detail="Prompt failed safety check",
        )

    # ── Content scope guard (LLM pre-check) ─────────────────────
    if not await check_scope(safe_prompt):
        ctx.warning("Prompt rejected by scope guard")
        raise HTTPException(
            status_code=400,
            detail="Prompt is outside the acceptable research scope",
        )

    # ── Build queue message ──────────────────────────────────────
    job_message = {
        "correlation_id": correlation_id,
        "prompt": safe_prompt,
        "source_hints": request.source_hints,
        "depth": request.depth,
        "callback_url": request.callback_url,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── Seed the SQL row so polling never 404s ───────────────────
    from persistence.sql_client import upsert_job
    await upsert_job({
        "correlation_id": correlation_id,
        "prompt": safe_prompt,
        "depth": request.depth,
        "status": "queued",
        "stage": "planner",
        "submitted_at": job_message["submitted_at"],
        "callback_url": request.callback_url,
        "original_message": job_message,
    })

    # ── Publish ──────────────────────────────────────────────────
    await enqueue_job(job_message)
    ctx.info("Research job accepted and enqueued")

    return ResearchAccepted(
        correlation_id=correlation_id,
        status="queued",
        poll_url=f"/research/{correlation_id}",
    )


@app.get(
    "/research/{correlation_id}",
    summary="Poll research job status",
)
async def get_research_status(correlation_id: str) -> dict:
    """
    Retrieve the current status of a research job by its correlation ID.
    Returns the full job record from Azure SQL.
    """
    from persistence.sql_client import get_job

    job = await get_job(correlation_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post(
    "/replay/{correlation_id}",
    summary="Replay a failed or dead-lettered job",
)
async def replay_job(correlation_id: str) -> dict:
    """
    Re-enqueue a failed or dead-lettered job using its original
    message payload.  The replayed job keeps its original
    ``correlation_id`` so existing checkpoints are preserved.
    """
    from persistence.sql_client import get_job

    job = await get_job(correlation_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") not in ("failed", "dead_lettered"):
        raise HTTPException(
            status_code=400,
            detail="Job is not eligible for replay "
            f"(current status: {job.get('status')})",
        )

    original = job.get("original_message", {})
    if not original:
        raise HTTPException(
            status_code=400,
            detail="Original message payload not available for replay",
        )

    await enqueue_job(original)
    logger.info(
        "Job replayed",
        extra={"correlation_id": correlation_id},
    )
    return {"status": "requeued", "correlation_id": correlation_id}


# ── Health check ─────────────────────────────────────────────────────


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok"}
