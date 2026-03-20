"""
Typed configuration loaded from environment variables / .env file.

Uses ``pydantic-settings`` for validation and type coercion.
A singleton ``get_settings()`` call caches the instance so it is only
built once per process.

Usage:
    from core.config import get_settings

    settings = get_settings()
    print(settings.azure_openai_deployment_name)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """All environment variables consumed by the research agent platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Azure AI Foundry / OpenAI
    # ------------------------------------------------------------------
    azure_ai_project_endpoint: str = ""
    azure_openai_project_endpoint: str = ""  # alias used in some .env files
    azure_openai_deployment_name: str = "gpt-4o"
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_openai_endpoint: str = ""

    @property
    def effective_project_endpoint(self) -> str:
        """Return whichever project endpoint is set."""
        return self.azure_openai_project_endpoint or self.azure_ai_project_endpoint

    # ------------------------------------------------------------------
    # Azure Storage (unified — used by Queue and Blob)
    # ------------------------------------------------------------------
    azure_storage_connection_string: str = ""
    azure_storage_account_name: str = ""
    azure_storage_queue_name: str = "research-jobs"
    azure_storage_poison_queue_name: str = "research-jobs-poison"
    blob_raw_sources_container: str = "raw-sources"

    # ------------------------------------------------------------------
    # Azure SQL Server
    # ------------------------------------------------------------------
    azure_sql_server: str = ""
    azure_sql_database: str = "ResearchAgentDB"
    azure_sql_username: str = ""
    azure_sql_password: str = ""
    azure_sql_connection_string: str = ""

    # ------------------------------------------------------------------
    # Azure AI Search
    # ------------------------------------------------------------------
    azure_search_endpoint: str = ""
    azure_search_key: str = ""
    azure_search_index: str = "internal-docs"

    # ------------------------------------------------------------------
    # External tools
    # ------------------------------------------------------------------
    tavily_api_key: str = ""

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------
    applicationinsights_connection_string: str = ""

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="console")  # "console" | "json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
