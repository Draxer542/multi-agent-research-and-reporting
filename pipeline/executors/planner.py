"""
Stage 1 — Planner Executor.

Receives the initial ``ResearchState``, calls Azure OpenAI to decompose
the research prompt into sub-queries, and forwards the enriched state
downstream.
"""

from __future__ import annotations

from agent_framework import WorkflowContext, executor

from pydantic import BaseModel, ValidationError
from typing import List

from pipeline.state import ResearchState
from persistence.sql_client import update_job_status
from monitoring.telemetry import tracer, timed_stage
from core.config import get_settings
from core.openai_client import get_openai_client
from core.logging import get_logger
from core.exceptions import ExecutorError

logger = get_logger(__name__, component="planner")


class TaskPlan(BaseModel):
    sub_queries: List[str]
    source_types: List[str]
    scope_notes: str
    estimated_complexity: str


PLANNER_SYSTEM_PROMPT = """\
You are a research planning agent. Given a research prompt, decompose it into
3–5 focused sub-queries that together cover the topic comprehensively.
For each sub-query, determine whether it needs live web data, internal documents,
or both. Return ONLY valid JSON matching the TaskPlan schema.
Do not include explanations or markdown.

TaskPlan schema:
{
  "sub_queries": ["..."],
  "source_types": ["web" | "internal" | "both"],
  "scope_notes": "...",
  "estimated_complexity": "low" | "medium" | "high"
}
"""


@executor(id="planner")
async def planner_executor(
    state: ResearchState, ctx: WorkflowContext[ResearchState]
) -> None:
    """Execute the Planner stage of the research pipeline."""
    correlation_id = state.correlation_id
    log = logger.bind(correlation_id=correlation_id, stage="planner")

    with tracer.start_as_current_span("planner_executor") as span:
        span.set_attribute("correlation_id", correlation_id)
        span.set_attribute("prompt_length", len(state.prompt))

        with timed_stage("planner", correlation_id):
            try:
                await update_job_status(correlation_id, "processing", stage="planner")
                settings = get_settings()
                client = get_openai_client()

                response = await client.chat.completions.create(
                    model=settings.azure_openai_deployment_name,
                    messages=[
                        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Research prompt: {state.prompt}"},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )

                raw = response.choices[0].message.content

                # Schema validation with correction retry
                try:
                    plan = TaskPlan.model_validate_json(raw)
                except ValidationError as ve:
                    log.warning("First parse failed, retrying with correction")
                    correction = await client.chat.completions.create(
                        model=settings.azure_openai_deployment_name,
                        messages=[
                            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                            {"role": "user", "content": f"Research prompt: {state.prompt}"},
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": f"Your response failed schema validation: {ve}. Return corrected JSON only."},
                        ],
                        response_format={"type": "json_object"},
                    )
                    plan = TaskPlan.model_validate_json(
                        correction.choices[0].message.content
                    )

                state.task_plan = plan.model_dump()
                state.status = "planned"
                state.stage = "gatherer"

                span.set_attribute("sub_query_count", len(plan.sub_queries))
                log.info(
                    "Planning complete",
                    extra={"sub_queries": len(plan.sub_queries)},
                )

                await ctx.send_message(state)

            except Exception as exc:
                log.error("Planner failed", extra={"error": str(exc)})
                raise ExecutorError(
                    f"Planner failed: {exc}",
                    executor_name="planner",
                    correlation_id=correlation_id,
                    stage="planner",
                    cause=exc,
                ) from exc
