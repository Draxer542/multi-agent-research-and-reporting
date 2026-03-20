"""
OpenTelemetry tracing and custom metrics for the Research Agent platform.

Uses the official ``azure-monitor-opentelemetry`` distro which provides
``configure_azure_monitor()`` — this automatically sets up:

- **Request tracking** (FastAPI HTTP requests → Application Insights "requests" table)
- **Dependency tracking** (outgoing httpx/requests calls → "dependencies" table)
- **Exception tracking** (unhandled exceptions → "exceptions" table)
- **Log forwarding** (Python logging → "traces" table)
- **Custom metrics** (pipeline counters/histograms → "customMetrics" table)

Usage::

    from monitoring.telemetry import setup_telemetry, tracer, metrics

    setup_telemetry()

    # Traces
    with tracer.start_as_current_span("my_op") as span:
        span.set_attribute("correlation_id", "abc-123")

    # Metrics
    metrics.job_counter.add(1, {"status": "complete"})
    metrics.job_duration.record(12.5, {"stage": "planner"})
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry import trace, metrics as otel_metrics

from core.logging import get_logger

logger = get_logger(__name__, component="telemetry")

_initialised = False


# ── Custom Metrics Container ─────────────────────────────────────────

@dataclass
class PipelineMetrics:
    """Holds all custom OpenTelemetry instruments for the pipeline."""

    _meter: object = None

    # Counters
    job_counter: object = None          # pipeline.job.count
    regather_counter: object = None     # pipeline.regather.count
    error_counter: object = None        # pipeline.error.count
    deadletter_counter: object = None   # pipeline.deadletter.count

    # Histograms
    job_duration: object = None         # pipeline.job.duration  (seconds)
    stage_duration: object = None       # pipeline.stage.duration (seconds)
    confidence_histogram: object = None # pipeline.confidence_score

    def _init_instruments(self, meter) -> None:
        self._meter = meter

        self.job_counter = meter.create_counter(
            name="pipeline.job.count",
            description="Total research jobs processed",
            unit="1",
        )
        self.regather_counter = meter.create_counter(
            name="pipeline.regather.count",
            description="Number of re-gather branch triggers",
            unit="1",
        )
        self.error_counter = meter.create_counter(
            name="pipeline.error.count",
            description="Number of pipeline failures",
            unit="1",
        )
        self.deadletter_counter = meter.create_counter(
            name="pipeline.deadletter.count",
            description="Messages moved to poison queue",
            unit="1",
        )
        self.job_duration = meter.create_histogram(
            name="pipeline.job.duration",
            description="End-to-end job processing duration",
            unit="s",
        )
        self.stage_duration = meter.create_histogram(
            name="pipeline.stage.duration",
            description="Per-executor stage duration",
            unit="s",
        )
        self.confidence_histogram = meter.create_histogram(
            name="pipeline.confidence_score",
            description="Distribution of final confidence scores",
            unit="1",
        )


# Module-level metrics singleton — instruments are no-ops until setup
metrics = PipelineMetrics()


# ── Timer Context Manager ────────────────────────────────────────────

@contextmanager
def timed_stage(stage_name: str, correlation_id: str = ""):
    """
    Context manager that records stage duration to the OTel histogram.

    Usage::

        with timed_stage("planner", correlation_id="abc"):
            ...  # executor logic
    """
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        if metrics.stage_duration:
            metrics.stage_duration.record(
                elapsed,
                {"stage": stage_name, "correlation_id": correlation_id},
            )


# ── Setup ────────────────────────────────────────────────────────────

def setup_telemetry() -> None:
    """
    Initialise OpenTelemetry with the Azure Monitor distro.

    Uses ``configure_azure_monitor()`` which automatically instruments:
    - FastAPI (incoming HTTP requests)
    - httpx / requests (outgoing HTTP calls)
    - Logging (Python log records)
    - Exceptions (unhandled errors)

    Safe to call multiple times — only the first invocation takes effect.
    """
    global _initialised
    if _initialised:
        return

    from core.config import get_settings
    conn_str = get_settings().applicationinsights_connection_string

    if not conn_str:
        logger.warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set — "
            "tracing and metrics disabled (no-op)"
        )
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)
        _initialised = True
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=conn_str,
            enable_live_metrics=True,
            logger_name="",  # capture all Python loggers
        )

        # Initialise custom pipeline instruments
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)

        logger.info(
            "Azure Monitor OpenTelemetry distro initialised "
            "(requests + dependencies + logs + metrics)"
        )

    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed — "
            "falling back to no-op telemetry"
        )
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)

    except Exception as exc:
        logger.error(
            "Failed to initialise Azure Monitor OpenTelemetry",
            extra={"error": str(exc)},
        )
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)

    _initialised = True


def instrument_app(app) -> None:
    """
    Kept for backward compatibility — ``configure_azure_monitor()`` already
    instruments FastAPI automatically via its bundled instrumentors.
    This is now a no-op but safe to call.
    """
    logger.debug("instrument_app called (auto-instrumentation already active via distro)")


# Module-level tracer — importable from anywhere.
# Returns a no-op tracer until setup_telemetry() is called.
tracer = trace.get_tracer("research-agent")
