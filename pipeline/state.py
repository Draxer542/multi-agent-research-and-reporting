"""
Cross-step pipeline state — message type for the MAF workflow.

A single ``ResearchState`` dataclass flows between executors as the
message type.  Each executor receives the state, performs its work,
mutates the relevant fields, and forwards it downstream via
``ctx.send_message()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ResearchState:
    """Accumulated state that flows through every pipeline executor."""

    # Identity
    correlation_id: str = ""
    prompt: str = ""

    # Status tracking
    status: str = "queued"
    stage: str = "planner"
    regather_count: int = 0

    # Pipeline data (populated progressively by each executor)
    task_plan: dict = field(default_factory=dict)
    raw_sources: list[dict] = field(default_factory=list)
    extracted_facts: list[dict] = field(default_factory=list)
    comparison_result: dict = field(default_factory=dict)
    confidence_score: float = 0.0
    report: dict = field(default_factory=dict)
    score: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    # Request metadata
    source_hints: list[str] = field(default_factory=list)
    depth: str = "standard"
    callback_url: Optional[str] = None
    submitted_at: str = ""
    completed_at: Optional[str] = None
    error_reason: Optional[str] = None
    original_message: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to a plain dict for SQL persistence."""
        from dataclasses import asdict
        return asdict(self)
