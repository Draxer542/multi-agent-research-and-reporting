"""
Pydantic request/response schemas for the Research Agent API.

These models enforce payload validation at the HTTP boundary and
provide OpenAPI documentation via FastAPI's automatic schema generation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional, List


class ResearchRequest(BaseModel):
    """Inbound research job request."""

    prompt: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="The research question or topic to investigate.",
    )
    source_hints: Optional[List[str]] = Field(
        default=None,
        description='Preferred source channels, e.g. ["web", "internal"].',
    )
    depth: str = Field(
        default="standard",
        pattern="^(quick|standard|deep)$",
        description='Research depth: "quick", "standard", or "deep".',
    )
    callback_url: Optional[str] = Field(
        default=None,
        description="Optional URL to POST the final report to on completion.",
    )


class ResearchAccepted(BaseModel):
    """Response returned when a research job is successfully enqueued."""

    correlation_id: str
    status: str
    poll_url: str


class JobStatus(BaseModel):
    """Lightweight status response for polling."""

    correlation_id: str
    status: str
    stage: str
    confidence_score: Optional[float] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    report: Optional[dict] = None
    score: Optional[dict] = None
    error_reason: Optional[str] = None
