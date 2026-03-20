"""
Base exception hierarchy for the Research Agent platform.

All project-specific exceptions derive from ``ResearchAgentError`` so
callers can catch the entire tree with a single except clause when needed.
Every exception optionally carries ``correlation_id`` and ``stage`` for
structured error reporting and telemetry tagging.
"""

from __future__ import annotations
from typing import Optional


class ResearchAgentError(Exception):
    """Root exception for the Research Agent platform."""

    def __init__(
        self,
        message: str = "",
        *,
        correlation_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        self.correlation_id = correlation_id
        self.stage = stage
        super().__init__(message)

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.correlation_id:
            parts.append(f"correlation_id={self.correlation_id}")
        if self.stage:
            parts.append(f"stage={self.stage}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Pipeline errors
# ---------------------------------------------------------------------------

class PipelineError(ResearchAgentError):
    """Errors during pipeline orchestration (workflow-level)."""


class ExecutorError(PipelineError):
    """Failure inside a specific pipeline executor."""

    def __init__(
        self,
        message: str = "",
        *,
        executor_name: str = "",
        correlation_id: Optional[str] = None,
        stage: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.executor_name = executor_name
        self.cause = cause
        super().__init__(
            message, correlation_id=correlation_id, stage=stage
        )


# ---------------------------------------------------------------------------
# Tool errors
# ---------------------------------------------------------------------------

class ToolError(ResearchAgentError):
    """External tool call failed (Tavily, Azure AI Search, etc.)."""

    def __init__(
        self,
        message: str = "",
        *,
        tool_name: str = "",
        correlation_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        self.tool_name = tool_name
        super().__init__(
            message, correlation_id=correlation_id, stage=stage
        )


# ---------------------------------------------------------------------------
# Persistence errors
# ---------------------------------------------------------------------------

class PersistenceError(ResearchAgentError):
    """Database or blob storage operation failed."""


# ---------------------------------------------------------------------------
# Queue errors
# ---------------------------------------------------------------------------

class QueueError(ResearchAgentError):
    """Azure Queue Storage publish or consume failed."""


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class SchemaValidationError(ResearchAgentError):
    """LLM response or input payload failed Pydantic schema validation."""


class PromptRejectedError(ResearchAgentError):
    """Prompt failed the injection filter or scope-guard check."""


# ---------------------------------------------------------------------------
# Retry errors
# ---------------------------------------------------------------------------

class RetryExhaustedError(ResearchAgentError):
    """All retry attempts exhausted — wraps the original exception."""

    def __init__(
        self,
        message: str = "",
        *,
        attempts: int = 0,
        correlation_id: Optional[str] = None,
        stage: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.attempts = attempts
        self.cause = cause
        super().__init__(
            message, correlation_id=correlation_id, stage=stage
        )
