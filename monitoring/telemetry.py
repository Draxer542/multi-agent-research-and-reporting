"""
OpenTelemetry tracing and custom metrics for the Research Agent platform.

Initialises a ``TracerProvider`` and ``MeterProvider`` and configures the
Azure Monitor exporter when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set.
In local development (env var absent), tracing and metrics are silent no-ops.

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

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from opentelemetry import trace, metrics as otel_metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

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
    Initialise OpenTelemetry with the Azure Monitor exporter.

    Safe to call multiple times — only the first invocation takes effect.
    If ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is not set, this function
    logs a warning and returns (tracing becomes a no-op).
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
        # Still create no-op instruments so call sites don't need guards
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)
        _initialised = True
        return

    try:
        from azure.monitor.opentelemetry.exporter import (
            AzureMonitorTraceExporter,
            AzureMonitorMetricExporter,
        )

        # Traces
        trace_exporter = AzureMonitorTraceExporter(connection_string=conn_str)
        trace_provider = TracerProvider()
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(trace_provider)

        # Metrics
        metric_exporter = AzureMonitorMetricExporter(connection_string=conn_str)
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=60_000
        )
        meter_provider = MeterProvider(metric_readers=[metric_reader])
        otel_metrics.set_meter_provider(meter_provider)

        # Initialise instruments
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)

        logger.info(
            "OpenTelemetry initialised with Azure Monitor "
            "(traces + metrics)"
        )
    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry-exporter not installed — "
            "tracing disabled"
        )
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)
    except Exception as exc:
        logger.error(
            "Failed to initialise OpenTelemetry",
            extra={"error": str(exc)},
        )
        meter = otel_metrics.get_meter("research-agent")
        metrics._init_instruments(meter)

    _initialised = True


# Module-level tracer — importable from anywhere.
# Returns a no-op tracer until setup_telemetry() is called.
tracer = trace.get_tracer("research-agent")
