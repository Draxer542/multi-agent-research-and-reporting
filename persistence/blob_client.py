"""
Azure Blob Storage client for archiving raw sources and final reports.

- Archives raw fetched content from the Source Gatherer to
  ``{correlation_id}/raw_sources.json``.
- Archives the final structured report from the Scorer to
  ``Reports/{correlation_id}/report.json``.

Uses the unified Azure Storage connection string.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from azure.storage.blob import BlobServiceClient

from core.config import get_settings
from core.logging import get_logger
from core.exceptions import PersistenceError

logger = get_logger(__name__, component="blob_client")


def _get_blob_service() -> BlobServiceClient:
    """Create a BlobServiceClient from the configured connection string."""
    settings = get_settings()
    return BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    )


async def archive_sources(
    correlation_id: str, sources: list
) -> None:
    """
    Upload raw source documents to Azure Blob Storage.

    Args:
        correlation_id: The job's correlation ID (used as blob prefix).
        sources: List of source dicts to archive.

    Raises:
        PersistenceError: If the upload fails.
    """
    settings = get_settings()
    ctx = logger.bind(correlation_id=correlation_id)

    try:
        blob_service = _get_blob_service()
        container = blob_service.get_container_client(
            settings.blob_raw_sources_container
        )

        blob_name = f"{correlation_id}/raw_sources.json"
        data = json.dumps(sources, indent=2).encode()
        container.upload_blob(
            name=blob_name, data=data, overwrite=True
        )

        ctx.info(
            "Raw sources archived",
            extra={"blob": blob_name, "source_count": len(sources)},
        )
    except Exception as exc:
        ctx.error("Blob archive failed", extra={"error": str(exc)})
        raise PersistenceError(
            f"Failed to archive sources: {exc}",
            correlation_id=correlation_id,
        ) from exc


async def archive_report(
    correlation_id: str,
    report: dict,
    score: dict,
) -> str:
    """
    Upload the final structured report to the Reports/ folder in blob storage.

    The blob path is ``Reports/{correlation_id}/report.json``.

    Args:
        correlation_id: Job correlation ID.
        report: The rich report dict (from writer executor).
        score: The scoring dict (from scorer executor).

    Returns:
        The blob name that was created.

    Raises:
        PersistenceError: If the upload fails.
    """
    settings = get_settings()
    ctx = logger.bind(correlation_id=correlation_id)

    try:
        blob_service = _get_blob_service()
        container = blob_service.get_container_client(
            settings.blob_raw_sources_container
        )

        # Build the final report envelope
        report_envelope = {
            "correlation_id": correlation_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report": report,
            "quality_score": score,
        }

        blob_name = f"Reports/{correlation_id}/report.json"
        data = json.dumps(report_envelope, indent=2, default=str).encode()
        container.upload_blob(
            name=blob_name, data=data, overwrite=True
        )

        ctx.info(
            "Report archived to blob",
            extra={"blob": blob_name},
        )
        return blob_name

    except Exception as exc:
        ctx.error("Report archive failed", extra={"error": str(exc)})
        raise PersistenceError(
            f"Failed to archive report: {exc}",
            correlation_id=correlation_id,
        ) from exc
