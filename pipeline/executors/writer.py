"""
Stage 5 — Writer Executor.

Produces the final structured research report matching the rich template:
executive summary with key takeaways, detailed findings with supporting
facts, structured agreements/conflicts, rich citations, and open questions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

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

logger = get_logger(__name__, component="writer")


# ── Rich report Pydantic models (matching report_structure.json) ──────────


class ExecutiveSummary(BaseModel):
    text: str
    word_count: int
    key_takeaways: List[str]


class SupportingFact(BaseModel):
    claim: str
    citation_ref: str


class Finding(BaseModel):
    finding_id: str
    sub_query: str
    summary: str
    detail: str
    confidence: float
    source_agreement: str  # "full" | "partial" | "contradicted"
    citations: List[str]
    supporting_facts: List[SupportingFact]


class Agreement(BaseModel):
    agreement_id: str
    topic: str
    statement: str
    supporting_citations: List[str]
    strength: str  # "strong" | "moderate" | "weak"


class ClaimSide(BaseModel):
    text: str
    citation_ref: str
    source_label: str


class Conflict(BaseModel):
    conflict_id: str
    topic: str
    claim_a: ClaimSide
    claim_b: ClaimSide
    severity: str  # "minor" | "major"
    resolution_status: str
    analyst_note: str


class OpenQuestion(BaseModel):
    question_id: str
    question: str
    origin: str  # "gap_in_coverage" | "conflict_unresolved" | "scope_limitation"
    linked_conflict_id: Optional[str] = None
    priority: str  # "high" | "medium" | "low"


class Citation(BaseModel):
    ref_id: str
    title: str
    authors: Optional[List[str]] = None
    venue: Optional[str] = None
    publication_year: Optional[str] = None
    source_type: str  # "web" | "internal"
    url: str
    file_name: Optional[str] = None
    section_referenced: Optional[str] = None
    relevance_score: Optional[float] = None


class ReportMetadata(BaseModel):
    report_id: str
    correlation_id: str
    title: str
    generated_at: str
    prompt: str
    depth: str
    overall_confidence: Optional[float] = None
    recommendation: Optional[str] = None


class ResearchReport(BaseModel):
    report_metadata: ReportMetadata
    executive_summary: ExecutiveSummary
    findings: List[Finding]
    agreements: List[Agreement]
    conflicts: List[Conflict]
    open_questions: List[OpenQuestion]
    citations: List[Citation]
    methodology_note: str


WRITER_SYSTEM_PROMPT = """\
You are a research report writing agent. Given extracted facts, a comparison analysis,
and citation sources, produce a comprehensive structured research report.

The report must follow this EXACT structure. Return ONLY valid JSON. No markdown.

{
  "report_metadata": {
    "report_id": "RPT-<short-uuid>",
    "correlation_id": "<from input>",
    "title": "descriptive report title",
    "generated_at": "ISO 8601 datetime",
    "prompt": "<original research prompt>",
    "depth": "<standard|deep|quick>"
  },
  "executive_summary": {
    "text": "concise 150-250 word overview of all findings",
    "word_count": <integer>,
    "key_takeaways": ["takeaway 1", "takeaway 2", "takeaway 3"]
  },
  "findings": [
    {
      "finding_id": "FND-001",
      "sub_query": "the original sub-query this finding addresses",
      "summary": "one-sentence summary",
      "detail": "detailed paragraph with supporting evidence",
      "confidence": 0.0-1.0,
      "source_agreement": "full" | "partial" | "contradicted",
      "citations": ["ref-1", "ref-2"],
      "supporting_facts": [{"claim": "specific fact", "citation_ref": "ref-1"}]
    }
  ],
  "agreements": [
    {
      "agreement_id": "AGR-001",
      "topic": "topic name",
      "statement": "what sources agree on",
      "supporting_citations": ["ref-1", "ref-2"],
      "strength": "strong" | "moderate" | "weak"
    }
  ],
  "conflicts": [
    {
      "conflict_id": "CON-001",
      "topic": "topic name",
      "claim_a": {"text": "claim text", "citation_ref": "ref-1", "source_label": "Source A"},
      "claim_b": {"text": "claim text", "citation_ref": "ref-2", "source_label": "Source B"},
      "severity": "minor" | "major",
      "resolution_status": "unresolved" | "resolved_in_source" | "partially_resolved",
      "analyst_note": "analysis of the conflict"
    }
  ],
  "open_questions": [
    {
      "question_id": "OQ-001",
      "question": "the unresolved question",
      "origin": "gap_in_coverage" | "conflict_unresolved" | "scope_limitation",
      "linked_conflict_id": "CON-001 or null",
      "priority": "high" | "medium" | "low"
    }
  ],
  "citations": [
    {
      "ref_id": "ref-1",
      "title": "source title",
      "authors": ["author name"] or null,
      "venue": "journal/site name" or null,
      "publication_year": "2024" or null,
      "source_type": "web" | "internal",
      "url": "https://...",
      "file_name": "document.pdf" or null,
      "section_referenced": "section name" or null,
      "relevance_score": 0.0-1.0 or null
    }
  ],
  "methodology_note": "Brief description of how sources were gathered and compared"
}

CRITICAL RULES:
- Every citation_ref value throughout the report (in supporting_facts, conflicts, agreements, etc.)
  MUST reference a ref_id from the citations array (e.g. "ref-1", "ref-2").
  NEVER use raw URLs as citation_ref values.
- Every source used in the report MUST appear in the citations array with its ref_id.
- Include BOTH web and internal sources in the citations. Tag internal documents with "source_type": "internal".
"""


@executor(id="writer")
async def writer_executor(
    state: ResearchState, ctx: WorkflowContext[ResearchState]
) -> None:
    """Execute the Writer stage of the research pipeline."""
    correlation_id = state.correlation_id
    log = logger.bind(correlation_id=correlation_id, stage="writer")

    with tracer.start_as_current_span("writer_executor") as span:
        span.set_attribute("correlation_id", correlation_id)

        with timed_stage("writer", correlation_id):
            try:
                await update_job_status(correlation_id, "processing", stage="writer")
                settings = get_settings()
                client = get_openai_client()

                source_meta = [
                    {
                        "url": s["url"],
                        "title": s.get("title", ""),
                        "source_type": s["source_type"],
                        "file_name": s.get("file_name", ""),
                    }
                    for s in state.raw_sources
                ]

                response = await client.chat.completions.create(
                    model=settings.azure_openai_deployment_name,
                    messages=[
                        {"role": "system", "content": WRITER_SYSTEM_PROMPT},
                        {"role": "user", "content": f"""\
Correlation ID: {correlation_id}
Prompt: {state.prompt}
Depth: {state.depth}
Task plan: {json.dumps(state.task_plan)}
Extracted facts: {json.dumps(state.extracted_facts)}
Comparison result: {json.dumps(state.comparison_result)}
Source metadata: {json.dumps(source_meta)}
"""},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    max_tokens=8000,
                )

                raw = response.choices[0].message.content
                try:
                    report = ResearchReport.model_validate_json(raw)
                except ValidationError as ve:
                    log.warning("Report parse failed, retrying with correction")
                    correction = await client.chat.completions.create(
                        model=settings.azure_openai_deployment_name,
                        messages=[
                            {"role": "system", "content": WRITER_SYSTEM_PROMPT},
                            {"role": "user", "content": f"Prompt: {state.prompt}\n..."},
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": f"Schema validation failed: {ve}. Return corrected JSON only."},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.2,
                        max_tokens=8000,
                    )
                    report = ResearchReport.model_validate_json(
                        correction.choices[0].message.content
                    )

                state.report = report.model_dump()
                state.status = "written"
                state.stage = "scorer"

                span.set_attribute("finding_count", len(report.findings))
                log.info(
                    "Report written",
                    extra={
                        "findings": len(report.findings),
                        "agreements": len(report.agreements),
                        "conflicts": len(report.conflicts),
                        "citations": len(report.citations),
                    },
                )

                await ctx.send_message(state)

            except Exception as exc:
                log.error("Writer failed", extra={"error": str(exc)})
                raise ExecutorError(
                    f"Writer failed: {exc}",
                    executor_name="writer",
                    correlation_id=correlation_id,
                    stage="writer",
                    cause=exc,
                ) from exc
