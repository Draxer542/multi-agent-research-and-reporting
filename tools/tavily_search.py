"""
Tavily Search API wrapper.

Provides a single ``tavily_search`` function that queries the Tavily
API for live web content, returning a list of structured result dicts
tagged with ``source_type="web"``.

Wrapped with tenacity retry for transient HTTP failures.
"""

from __future__ import annotations

from typing import List, Dict

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import get_settings
from core.logging import get_logger
from core.exceptions import ToolError

logger = get_logger(__name__, component="tavily")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
)
async def tavily_search(
    query: str, max_results: int = 5
) -> List[Dict]:
    """
    Search the web via the Tavily API.

    Args:
        query: The search query string.
        max_results: Maximum results to return (default 5).

    Returns:
        List of dicts with keys: ``source_type``, ``url``, ``title``,
        ``content``, ``score``.

    Raises:
        ToolError: If all retry attempts fail.
    """
    settings = get_settings()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "include_raw_content": True,
                    "max_results": max_results,
                },
                timeout=20,
            )
            resp.raise_for_status()

        results = resp.json().get("results", [])
        return [
            {
                "source_type": "web",
                "url": r["url"],
                "title": r.get("title", ""),
                "content": r.get("raw_content") or r.get("content", ""),
                "score": r.get("score", 0),
            }
            for r in results
        ]

    except Exception as exc:
        logger.error("Tavily search failed", extra={"error": str(exc)})
        raise ToolError(
            f"Tavily search failed: {exc}",
            tool_name="tavily",
        ) from exc
