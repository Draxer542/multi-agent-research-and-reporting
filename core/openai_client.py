"""
Shared Azure OpenAI client factory.

Provides a ``get_openai_client`` function that creates an ``AsyncAzureOpenAI``
client using the API key from settings.  Used by all LLM-calling executors.
"""

from __future__ import annotations

from openai import AsyncAzureOpenAI

from core.config import get_settings


def get_openai_client() -> AsyncAzureOpenAI:
    """Create an AsyncAzureOpenAI client using API key authentication."""
    settings = get_settings()

    endpoint = settings.effective_project_endpoint or settings.azure_openai_endpoint
    if not endpoint:
        raise RuntimeError(
            "No Azure OpenAI endpoint configured. Set AZURE_OPENAI_PROJECT_ENDPOINT "
            "or AZURE_OPENAI_ENDPOINT in .env"
        )

    return AsyncAzureOpenAI(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=endpoint,
    )
