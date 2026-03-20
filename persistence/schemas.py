"""
SQL table schemas and migration helpers.

Provides a ``run_migrations`` function that reads and executes DDL
scripts from the ``migrations/`` directory.  Uses synchronous ``pyodbc``
since migrations are a one-off operation that doesn't benefit from async.

Standalone usage::

    python -m persistence.schemas
"""

from __future__ import annotations

from pathlib import Path

import pyodbc

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__, component="schemas")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(connection_string: str | None = None) -> None:
    """
    Execute all pending SQL migration scripts in order.

    Reads ``.sql`` files from the ``migrations/`` directory sorted by
    filename (expected naming: ``V1__description.sql``, ``V2__...``, etc.)
    and executes each one.  Designed for initial schema setup — does NOT
    track which migrations have already been applied.

    Args:
        connection_string: ODBC connection string.  If ``None``, reads
                           from ``get_settings().azure_sql_connection_string``.
    """
    if connection_string is None:
        settings = get_settings()
        connection_string = settings.azure_sql_connection_string

    sql_files = sorted(MIGRATIONS_DIR.glob("V*__*.sql"))

    if not sql_files:
        logger.warning("No migration files found in %s", MIGRATIONS_DIR)
        return

    conn = pyodbc.connect(connection_string, autocommit=True)
    try:
        cur = conn.cursor()
        for sql_file in sql_files:
            logger.info(
                "Running migration",
                extra={"file": sql_file.name},
            )
            ddl = sql_file.read_text(encoding="utf-8")

            # Split on GO if present (SQL Server batch separator)
            batches = [
                b.strip()
                for b in ddl.split("\nGO\n")
                if b.strip()
            ]
            if len(batches) <= 1:
                batches = [ddl]

            for batch in batches:
                try:
                    cur.execute(batch)
                except Exception as exc:
                    logger.error(
                        "Migration batch failed",
                        extra={
                            "file": sql_file.name,
                            "error": str(exc),
                        },
                    )
                    raise

        logger.info("All migrations applied successfully")
    finally:
        conn.close()


if __name__ == "__main__":
    run_migrations()
