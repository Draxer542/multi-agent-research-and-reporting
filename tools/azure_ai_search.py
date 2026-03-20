"""
Azure AI Search (internal documents) retriever.

Queries the configured Azure AI Search index for internal documents
using semantic search, returning tagged result dicts.

Wrapped with tenacity retry for transient Azure failures.
"""

from __future__ import annotations

from typing import List, Dict

from azure.search.documents.aio import SearchClient
from azure.core.credentials import AzureKeyCredential
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import get_settings
from core.logging import get_logger
from core.exceptions import ToolError

logger = get_logger(__name__, component="azure_ai_search")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Exception,)),
)
async def internal_search(
    query: str, top: int = 5
) -> List[Dict]:
    """
    Search the internal document corpus via Azure AI Search.

    Args:
        query: The search query string.
        top: Max results to return (default 5).

    Returns:
        List of dicts with keys: ``source_type``, ``url``, ``title``,
        ``content``, ``doc_id``, ``last_updated``.

    Raises:
        ToolError: If all retry attempts fail.
    """
    settings = get_settings()

    try:
        client = SearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_index,
            credential=AzureKeyCredential(settings.azure_search_key),
        )
        async with client:
            results = await client.search(
                search_text=query,
                top=top,
                query_type="semantic",
                semantic_configuration_name="default",
                select=["id", "title", "content", "source_url", "last_updated"],
            )
            docs = []
            async for r in results:
                docs.append(
                    {
                        "source_type": "internal",
                        "url": r.get("source_url", ""),
                        "title": r.get("title", ""),
                        "content": r.get("content", ""),
                        "doc_id": r.get("id", ""),
                        "last_updated": r.get("last_updated", ""),
                    }
                )
        return docs

    except Exception as exc:
        logger.error(
            "Azure AI Search failed", extra={"error": str(exc)}
        )
        raise ToolError(
            f"Azure AI Search failed: {exc}",
            tool_name="azure_ai_search",
        ) from exc
