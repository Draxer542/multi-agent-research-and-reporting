"""
Stage 3 — Extractor Executor.

Iterates over raw sources and extracts structured, citable facts using
Azure OpenAI with Pydantic schema enforcement.
"""

from __future__ import annotations

import json

from agent_framework import WorkflowContext, executor

from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional

from pipeline.state import ResearchState
from persistence.sql_client import update_job_status
from monitoring.telemetry import tracer, timed_stage
from core.config import get_settings
from core.openai_client import get_openai_client
from core.logging import get_logger
from core.exceptions import ExecutorError

logger = get_logger(__name__, component="extractor")


class ExtractedFact(BaseModel):
    claim: str
    source_ref: str
    source_type: str
    date: Optional[str] = None
    confidence_raw: float = Field(ge=0, le=1)
    topic_tag: str


class ExtractionResult(BaseModel):
    facts: List[ExtractedFact]


EXTRACTOR_SYSTEM_PROMPT = """\
You are a fact extraction agent. Given a source document and a list of sub-queries,
extract all distinct factual claims relevant to those sub-queries.
For each claim, record its source reference, an estimated confidence (0–1),
and tag it to the most relevant sub-query topic.
Return ONLY valid JSON matching the ExtractionResult schema. No markdown.

ExtractionResult schema:
{
  "facts": [
    {
      "claim": "...",
      "source_ref": "url or doc_id",
      "source_type": "web" | "internal",
      "date": "optional date string",
      "confidence_raw": 0.0-1.0,
      "topic_tag": "relevant sub-query"
    }
  ]
}
"""


@executor(id="extractor")
async def extractor_executor(
    state: ResearchState, ctx: WorkflowContext[ResearchState]
) -> None:
    """Execute the Extractor stage of the research pipeline."""
    correlation_id = state.correlation_id
    log = logger.bind(correlation_id=correlation_id, stage="extractor")

    with tracer.start_as_current_span("extractor_executor") as span:
        span.set_attribute("correlation_id", correlation_id)

        with timed_stage("extractor", correlation_id):
            try:
                await update_job_status(correlation_id, "processing", stage="extractor")
                settings = get_settings()
                client = get_openai_client()
                all_facts = []

                for source in state.raw_sources:
                    if not source.get("content", "").strip():
                        continue

                    user_message = f"""\
Sub-queries: {json.dumps(state.task_plan['sub_queries'])}

Source URL: {source['url']}
Source type: {source['source_type']}
Content:
{source['content'][:4000]}
"""
                    response = await client.chat.completions.create(
                        model=settings.azure_openai_deployment_name,
                        messages=[
                            {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
                            {"role": "user", "content": user_message},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1,
                    )

                    raw = response.choices[0].message.content
                    try:
                        result = ExtractionResult.model_validate_json(raw)
                    except ValidationError as ve:
                        log.warning("Extraction parse failed, retrying with correction")
                        correction = await client.chat.completions.create(
                            model=settings.azure_openai_deployment_name,
                            messages=[
                                {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
                                {"role": "user", "content": user_message},
                                {"role": "assistant", "content": raw},
                                {"role": "user", "content": f"Schema validation failed: {ve}. Return corrected JSON only."},
                            ],
                            response_format={"type": "json_object"},
                        )
                        result = ExtractionResult.model_validate_json(
                            correction.choices[0].message.content
                        )

                    all_facts.extend(result.facts)

                state.extracted_facts = [f.model_dump() for f in all_facts]
                state.status = "extracted"
                state.stage = "comparator"

                span.set_attribute("fact_count", len(all_facts))
                log.info(
                    "Extraction complete",
                    extra={"fact_count": len(all_facts)},
                )

                await ctx.send_message(state)

            except Exception as exc:
                log.error("Extractor failed", extra={"error": str(exc)})
                raise ExecutorError(
                    f"Extractor failed: {exc}",
                    executor_name="extractor",
                    correlation_id=correlation_id,
                    stage="extractor",
                    cause=exc,
                ) from exc
