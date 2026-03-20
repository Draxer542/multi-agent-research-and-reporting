"""
Azure Queue Storage consumer loop (worker process).

Polls the research-jobs queue, dispatches messages through the MAF
workflow, persists results, and handles failures with retry / dead-letter.

Run as a standalone process::

    python -m worker.queue_worker
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
from azure.storage.queue import QueueClient

from pipeline.workflow import build_workflow
from pipeline.state import ResearchState
from persistence.sql_client import (
    update_job_status,
    upsert_job,
    persist_final_output,
)
from monitoring.telemetry import setup_telemetry, metrics
from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__, component="worker")

VISIBILITY_TIMEOUT = 300    # 5 minutes per attempt


async def process_message(message: dict) -> None:
    """Process a single research job message through the full pipeline."""
    correlation_id = message["correlation_id"]
    ctx = logger.bind(correlation_id=correlation_id)
    job_start = time.monotonic()

    try:
        workflow = build_workflow()

        # Build initial state as a dataclass
        initial_state = ResearchState(
            correlation_id=correlation_id,
            prompt=message["prompt"],
            source_hints=message.get("source_hints", []),
            depth=message.get("depth", "standard"),
            callback_url=message.get("callback_url"),
            status="processing",
            stage="planner",
            regather_count=0,
            flags=[],
            original_message=message,
            submitted_at=message.get("submitted_at", ""),
        )

        # Create initial job row
        await upsert_job(initial_state.to_dict())
        await update_job_status(correlation_id, "processing")

        ctx.info("Starting pipeline")

        # Run workflow — MAF returns events; we extract the output
        events = await workflow.run(initial_state)
        outputs = events.get_outputs()

        if not outputs:
            raise RuntimeError("Workflow completed with no output")

        final_state = outputs[0]  # ResearchState yielded by scorer

        # Atomically persist all four SQL tables
        await persist_final_output(final_state.to_dict())

        # ── Emit metrics ─────────────────────────────────────────
        elapsed = time.monotonic() - job_start
        if metrics.job_counter:
            metrics.job_counter.add(1, {"status": "complete"})
        if metrics.job_duration:
            metrics.job_duration.record(elapsed, {"status": "complete"})
        if metrics.confidence_histogram:
            metrics.confidence_histogram.record(final_state.confidence_score)
        if final_state.regather_count > 0 and metrics.regather_counter:
            metrics.regather_counter.add(final_state.regather_count)

        ctx.info("Job completed successfully", extra={"duration_s": elapsed})

        # Optional callback notification
        callback_url = message.get("callback_url")
        if callback_url:
            try:
                async with httpx.AsyncClient() as http:
                    await http.post(
                        callback_url,
                        json={
                            "correlation_id": correlation_id,
                            "status": "complete",
                            "report": final_state.report,
                            "score": final_state.score,
                        },
                        timeout=10,
                    )
                ctx.info("Callback notification sent")
            except Exception as cb_exc:
                ctx.warning(
                    "Callback notification failed",
                    extra={"error": str(cb_exc)},
                )

    except Exception as exc:
        import traceback
        traceback.print_exc()
        elapsed = time.monotonic() - job_start
        if metrics.error_counter:
            metrics.error_counter.add(1, {"stage": "pipeline"})
        if metrics.job_duration:
            metrics.job_duration.record(elapsed, {"status": "failed"})

        ctx.error("Job failed", extra={"error": str(exc)})
        await update_job_status(
            correlation_id,
            "failed",
            error_reason=str(exc),
        )
        raise


async def run_worker() -> None:
    """Main worker loop — poll queue indefinitely."""
    setup_telemetry()
    settings = get_settings()

    queue_client = QueueClient.from_connection_string(
        conn_str=settings.azure_storage_connection_string,
        queue_name=settings.azure_storage_queue_name,
    )

    logger.info("Worker started — polling queue")

    while True:
        messages = queue_client.receive_messages(
            max_messages=1,
            visibility_timeout=VISIBILITY_TIMEOUT,
        )
        processed = False

        for msg in messages:
            processed = True
            try:
                payload = json.loads(
                    base64.b64decode(msg.content).decode()
                )
                await process_message(payload)
                # Delete on success
                queue_client.delete_message(msg)
            except Exception:
                # Message stays in queue for retry / poison
                if metrics.deadletter_counter:
                    dequeue_count = getattr(msg, "dequeue_count", 0)
                    if dequeue_count >= 4:
                        metrics.deadletter_counter.add(1)

        if not processed:
            await asyncio.sleep(5)  # Back off when queue is empty


if __name__ == "__main__":
    asyncio.run(run_worker())
