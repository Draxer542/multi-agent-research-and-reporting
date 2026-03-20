"""
Centralized logging configuration for the Research Agent platform.

Provides structured JSON logging (production) or human-readable console
logging (development). Every logger carries the module name and supports
correlation-ID binding for per-job traceability.

Usage:
    from core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("Stage complete", correlation_id="abc-123", stage="planner")
"""

import logging
import os
import sys
import json
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("LOG_FORMAT", "console")  # "console" | "json"


# ---------------------------------------------------------------------------
# JSON Formatter — for production / Azure Monitor ingestion
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Emits each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields passed via `logger.info("msg", extra={...})`
        # or via the CorrelationAdapter
        for key in ("correlation_id", "stage", "component"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Console Formatter — for local development
# ---------------------------------------------------------------------------

CONSOLE_FORMAT = (
    "%(asctime)s │ %(levelname)-8s │ %(name)-30s │ %(message)s"
)


# ---------------------------------------------------------------------------
# Correlation-ID Adapter
# ---------------------------------------------------------------------------

class CorrelationAdapter(logging.LoggerAdapter):
    """
    Logger adapter that injects ``correlation_id`` into every log record
    so callers don't need to pass it as ``extra`` every time.

    Usage:
        logger = get_logger(__name__)
        ctx_logger = logger.bind(correlation_id="abc-123", stage="planner")
        ctx_logger.info("Planning complete")  # correlation_id auto-attached
    """

    def process(
        self, msg: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.update(self.extra)
        return msg, kwargs

    def bind(self, **new_context: Any) -> "CorrelationAdapter":
        """Return a new adapter with additional context fields merged in."""
        merged = {**self.extra, **new_context}
        return CorrelationAdapter(self.logger, merged)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_configured = False


def _configure_root() -> None:
    """One-time root logger configuration."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    if LOG_FORMAT == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))

    root.addHandler(handler)

    # Silence noisy Azure SDK loggers (QuickPulse pings, exporter HTTP calls)
    for noisy in (
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.monitor.opentelemetry.exporter._quickpulse",
        "azure.monitor.opentelemetry.exporter",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str, **initial_context: Any) -> CorrelationAdapter:
    """
    Return a correlation-aware logger for the given module name.

    Args:
        name: Typically ``__name__`` of the calling module.
        **initial_context: Optional default context fields
            (e.g. ``component="api"``).

    Returns:
        A ``CorrelationAdapter`` that supports ``.bind()`` for adding
        correlation IDs mid-request.
    """
    _configure_root()
    return CorrelationAdapter(logging.getLogger(name), initial_context)
