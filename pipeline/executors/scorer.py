"""
Stage 6 — Confidence Scorer Executor.

Applies deterministic heuristics (source diversity, coverage, conflict ratio)
to produce the final composite quality score and recommendation.  This is the
terminal executor that yields the workflow output.
"""

from __future__ import annotations

from typing import Any

from agent_framework import WorkflowContext, executor
from typing_extensions import Never

from pipeline.state import ResearchState
from persistence.sql_client import update_job_status
from persistence.blob_client import archive_report
from monitoring.telemetry import tracer, timed_stage
from core.logging import get_logger
from core.exceptions import ExecutorError

logger = get_logger(__name__, component="scorer")


@executor(id="scorer")
async def scorer_executor(
    state: ResearchState, ctx: WorkflowContext[Never, ResearchState]
) -> None:
    """Execute the Scorer stage — final executor that yields workflow output."""
    correlation_id = state.correlation_id
    log = logger.bind(correlation_id=correlation_id, stage="scorer")

    with tracer.start_as_current_span("scorer_executor") as span:
        span.set_attribute("correlation_id", correlation_id)

        with timed_stage("scorer", correlation_id):
            try:
                await update_job_status(correlation_id, "processing", stage="scorer")

                facts = state.extracted_facts
                comparison = state.comparison_result
                sub_queries = state.task_plan.get("sub_queries", [])

                # Deterministic heuristics
                unique_domains = len(set(
                    f["source_ref"].split("/")[2]
                    if f["source_ref"].startswith("http") else "internal"
                    for f in facts
                ))
                total_possible = len(facts)
                source_diversity = min(
                    unique_domains / max(total_possible, 1), 1.0
                )

                topics_with_multi = sum(
                    1 for sq in sub_queries
                    if sum(1 for f in facts if f.get("topic_tag") == sq) >= 2
                )
                coverage_score = topics_with_multi / max(len(sub_queries), 1)

                conflicted = comparison.get("conflicted_points", [])
                agreed = comparison.get("agreed_points", [])
                conflict_ratio = len(conflicted) / max(
                    len(agreed) + len(conflicted), 1
                )

                # Flags
                flags = list(state.flags)  # preserve existing flags
                if len(facts) < 5:
                    flags.append("low_fact_count")
                if unique_domains < 2:
                    flags.append("single_source_domain")
                if any(
                    c.get("severity") == "major" for c in conflicted
                ):
                    flags.append("unresolved_major_conflict")
                if coverage_score < 0.5:
                    flags.append("low_coverage")

                # Composite score
                overall = round(
                    (source_diversity * 0.25)
                    + (coverage_score * 0.35)
                    + ((1 - conflict_ratio) * 0.25)
                    + (state.confidence_score * 0.15),
                    4,
                )

                recommendation = (
                    "publish" if overall >= 0.70
                    else "review" if overall >= 0.45
                    else "reject"
                )

                score_data = {
                    "overall_confidence": overall,
                    "source_diversity": source_diversity,
                    "coverage_score": coverage_score,
                    "conflict_ratio": conflict_ratio,
                    "flags": flags,
                    "recommendation": recommendation,
                }

                state.score = score_data
                state.flags = flags
                state.status = "scored"
                state.stage = "complete"

                # Inject final scores into report metadata
                if state.report and "report_metadata" in state.report:
                    state.report["report_metadata"]["overall_confidence"] = overall
                    state.report["report_metadata"]["recommendation"] = recommendation

                # Persist report to blob storage (Reports/{cid}/report.json)
                try:
                    blob_name = await archive_report(
                        correlation_id, state.report, score_data
                    )
                    log.info("Report persisted to blob", extra={"blob": blob_name})
                except Exception as blob_exc:
                    log.warning(
                        "Report blob persistence failed (non-fatal)",
                        extra={"error": str(blob_exc)},
                    )

                span.set_attribute("overall_confidence", overall)
                span.set_attribute("recommendation", recommendation)
                log.info(
                    "Scoring complete",
                    extra={
                        "overall_confidence": overall,
                        "recommendation": recommendation,
                        "flags": flags,
                    },
                )

                # Terminal executor — yield output
                await ctx.yield_output(state)

            except Exception as exc:
                log.error("Scorer failed", extra={"error": str(exc)})
                raise ExecutorError(
                    f"Scorer failed: {exc}",
                    executor_name="scorer",
                    correlation_id=correlation_id,
                    stage="scorer",
                    cause=exc,
                ) from exc
