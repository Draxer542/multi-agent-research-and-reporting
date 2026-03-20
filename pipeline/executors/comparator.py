"""
Stage 4 — Comparator Executor.

Groups facts by topic, identifies agreements and conflicts across sources,
and computes a confidence score that drives conditional branching.

Outputs rich structured agreement/conflict data including IDs, strength
levels, resolution status, and analyst notes for the downstream writer.
"""

from __future__ import annotations

import json

from agent_framework import WorkflowContext, executor

from pydantic import BaseModel, ValidationError
from typing import List, Optional

from pipeline.state import ResearchState
from persistence.sql_client import update_job_status
from monitoring.telemetry import tracer, timed_stage
from core.config import get_settings
from core.openai_client import get_openai_client
from core.logging import get_logger
from core.exceptions import ExecutorError

logger = get_logger(__name__, component="comparator")


class AgreementPoint(BaseModel):
    agreement_id: str
    topic: str
    statement: str
    supporting_citations: List[str]
    strength: str  # "strong" | "moderate" | "weak"


class ClaimDetail(BaseModel):
    text: str
    citation_ref: str
    source_label: str


class ConflictPoint(BaseModel):
    conflict_id: str
    topic: str
    claim_a: ClaimDetail
    claim_b: ClaimDetail
    severity: str  # "minor" | "major"
    resolution_status: str  # "unresolved" | "resolved_in_source" | "partially_resolved"
    analyst_note: str


class ComparisonResult(BaseModel):
    agreements: List[AgreementPoint]
    conflicts: List[ConflictPoint]
    open_questions: List[str]
    confidence_score: float
    coverage_ratio: float


COMPARATOR_SYSTEM_PROMPT = """\
You are a research comparator agent. Given a list of extracted facts from multiple sources
(both web and internal documents), identify:

1. **Agreements**: Points where sources converge. Include citation references and strength.
2. **Conflicts**: Points where sources disagree. Include both claims with citations,
   severity assessment, and resolution status.
3. **Open Questions**: Gaps or unresolved issues.
4. **Scores**: confidence_score (0–1) and coverage_ratio (0–1).

Return ONLY valid JSON matching the ComparisonResult schema. No markdown.

ComparisonResult schema:
{
  "agreements": [
    {
      "agreement_id": "AGR-001",
      "topic": "topic name",
      "statement": "what sources agree on",
      "supporting_citations": ["url-or-ref-1", "url-or-ref-2"],
      "strength": "strong" | "moderate" | "weak"
    }
  ],
  "conflicts": [
    {
      "conflict_id": "CON-001",
      "topic": "topic name",
      "claim_a": {"text": "claim from source A", "citation_ref": "url-or-ref", "source_label": "Source A Name"},
      "claim_b": {"text": "claim from source B", "citation_ref": "url-or-ref", "source_label": "Source B Name"},
      "severity": "minor" | "major",
      "resolution_status": "unresolved" | "resolved_in_source" | "partially_resolved",
      "analyst_note": "brief analysis of the conflict"
    }
  ],
  "open_questions": ["question arising from a gap or conflict"],
  "confidence_score": 0.0-1.0,
  "coverage_ratio": 0.0-1.0
}
"""


@executor(id="comparator")
async def comparator_executor(
    state: ResearchState, ctx: WorkflowContext[ResearchState]
) -> None:
    """Execute the Comparator stage of the research pipeline."""
    correlation_id = state.correlation_id
    log = logger.bind(correlation_id=correlation_id, stage="comparator")

    with tracer.start_as_current_span("comparator_executor") as span:
        span.set_attribute("correlation_id", correlation_id)

        with timed_stage("comparator", correlation_id):
            try:
                await update_job_status(correlation_id, "processing", stage="comparator")
                settings = get_settings()
                client = get_openai_client()

                response = await client.chat.completions.create(
                    model=settings.azure_openai_deployment_name,
                    messages=[
                        {"role": "system", "content": COMPARATOR_SYSTEM_PROMPT},
                        {"role": "user", "content": f"""\
Sub-queries: {json.dumps(state.task_plan['sub_queries'])}
Extracted facts: {json.dumps(state.extracted_facts)}
"""},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                    max_tokens=4000,
                )

                raw = response.choices[0].message.content
                try:
                    result = ComparisonResult.model_validate_json(raw)
                except ValidationError as ve:
                    log.warning("Comparison parse failed, retrying with correction")
                    correction = await client.chat.completions.create(
                        model=settings.azure_openai_deployment_name,
                        messages=[
                            {"role": "system", "content": COMPARATOR_SYSTEM_PROMPT},
                            {"role": "user", "content": f"Sub-queries: {json.dumps(state.task_plan['sub_queries'])}\nExtracted facts: {json.dumps(state.extracted_facts)}"},
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": f"Schema validation failed: {ve}. Return corrected JSON only."},
                        ],
                        response_format={"type": "json_object"},
                    )
                    result = ComparisonResult.model_validate_json(
                        correction.choices[0].message.content
                    )

                state.comparison_result = result.model_dump()
                state.confidence_score = result.confidence_score
                state.status = "compared"

                # Branch decision — read by conditional edge
                if result.confidence_score < 0.55:
                    state.stage = "regather"
                    state.regather_count += 1
                else:
                    state.stage = "writer"

                span.set_attribute("confidence_score", result.confidence_score)
                span.set_attribute("conflict_count", len(result.conflicts))
                log.info(
                    "Comparison complete",
                    extra={
                        "confidence_score": result.confidence_score,
                        "agreement_count": len(result.agreements),
                        "conflict_count": len(result.conflicts),
                        "branch": state.stage,
                    },
                )

                await ctx.send_message(state)

            except Exception as exc:
                log.error("Comparator failed", extra={"error": str(exc)})
                raise ExecutorError(
                    f"Comparator failed: {exc}",
                    executor_name="comparator",
                    correlation_id=correlation_id,
                    stage="comparator",
                    cause=exc,
                ) from exc
