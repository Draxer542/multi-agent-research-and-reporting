"""
Azure Queue Storage publisher.

Encodes job messages as base64 JSON and publishes them to the
configured Azure Queue Storage queue using the unified storage
connection string.
"""

from __future__ import annotations

import base64
import json

from azure.storage.queue import QueueClient

from core.config import get_settings
from core.logging import get_logger
from core.exceptions import QueueError

logger = get_logger(__name__, component="queue_client")


async def enqueue_job(message: dict) -> None:
    """
    Publish a research job message to Azure Queue Storage.

    The message is JSON-serialised and base64-encoded (Azure Queue
    Storage requires base64 for binary-safe transport).

    Raises:
        QueueError: If the message could not be published.
    """
    settings = get_settings()
    correlation_id = message.get("correlation_id", "unknown")
    ctx = logger.bind(correlation_id=correlation_id)

    try:
        queue_client = QueueClient.from_connection_string(
            conn_str=settings.azure_storage_connection_string,
            queue_name=settings.azure_storage_queue_name,
        )

        encoded = base64.b64encode(
            json.dumps(message).encode()
        ).decode()

        queue_client.send_message(encoded, visibility_timeout=30)
        ctx.info("Job enqueued successfully")

    except Exception as exc:
        ctx.error("Failed to enqueue job", extra={"error": str(exc)})
        raise QueueError(
            f"Failed to enqueue job: {exc}",
            correlation_id=correlation_id,
        ) from exc
