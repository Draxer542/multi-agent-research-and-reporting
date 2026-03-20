"""
Stage 2 — Source Gatherer Executor.

Executes sub-queries in parallel against Tavily (web) and Azure AI Search
(internal docs).  Results are tagged with source metadata, deduplicated
by URL, and archived to Azure Blob Storage.
"""

from __future__ import annotations

import asyncio

from agent_framework import WorkflowContext, executor

from pipeline.state import ResearchState
from tools.tavily_search import tavily_search
from tools.azure_ai_search import internal_search
from tools.content_cleaner import clean_web_content, clean_internal_content
from persistence.blob_client import archive_sources
from persistence.sql_client import update_job_status
from monitoring.telemetry import tracer, timed_stage
from core.logging import get_logger
from core.exceptions import ExecutorError

logger = get_logger(__name__, component="gatherer")


@executor(id="gatherer")
async def gatherer_executor(
    state: ResearchState, ctx: WorkflowContext[ResearchState]
) -> None:
    """Execute the Source Gatherer stage of the research pipeline."""
    correlation_id = state.correlation_id
    log = logger.bind(correlation_id=correlation_id, stage="gatherer")

    with tracer.start_as_current_span("gatherer_executor") as span:
        span.set_attribute("correlation_id", correlation_id)

        with timed_stage("gatherer", correlation_id):
            try:
                await update_job_status(correlation_id, "processing", stage="gatherer")

                plan = state.task_plan

                async def fetch_query(sub_query: str, source_types: list) -> list:
                    tasks = []
                    if "web" in source_types or "both" in source_types:
                        tasks.append(tavily_search(sub_query))
                    if "internal" in source_types or "both" in source_types:
                        tasks.append(internal_search(sub_query))
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    merged = []
                    for r in results:
                        if isinstance(r, Exception):
                            log.warning(
                                "Source fetch failed for sub-query",
                                extra={"sub_query": sub_query, "error": str(r)},
                            )
                            continue
                        merged.extend(r)
                    return merged

                # Parallel fetch across all sub-queries
                fetch_tasks = [
                    fetch_query(sq, plan["source_types"])
                    for sq in plan["sub_queries"]
                ]
                query_results = await asyncio.gather(*fetch_tasks)

                all_sources = []
                for batch in query_results:
                    all_sources.extend(batch)

                # Deduplicate by URL
                seen_urls: set[str] = set()
                deduped = []
                for s in all_sources:
                    url = s.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        deduped.append(s)

                # Clean content before archiving and downstream processing
                for s in deduped:
                    if s.get("source_type") == "web":
                        s["content"] = clean_web_content(s.get("content", ""))
                    else:
                        s["content"] = clean_internal_content(s.get("content", ""))

                # Archive raw sources to Blob Storage
                await archive_sources(correlation_id, deduped)

                state.raw_sources = deduped
                state.status = "gathered"
                state.stage = "extractor"

                span.set_attribute("source_count", len(deduped))
                log.info(
                    "Source gathering complete",
                    extra={"total_sources": len(deduped)},
                )

                await ctx.send_message(state)

            except Exception as exc:
                log.error("Gatherer failed", extra={"error": str(exc)})
                raise ExecutorError(
                    f"Gatherer failed: {exc}",
                    executor_name="gatherer",
                    correlation_id=correlation_id,
                    stage="gatherer",
                    cause=exc,
                ) from exc
