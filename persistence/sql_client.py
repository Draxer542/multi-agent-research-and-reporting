"""
Azure SQL Server async client.

Provides CRUD helpers for the Jobs, Facts, ConflictPoints, and Citations
tables using ``aioodbc`` connection pooling.  All queries use parameterised
statements — no string interpolation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import aioodbc

from core.config import get_settings
from core.logging import get_logger
from core.exceptions import PersistenceError

logger = get_logger(__name__, component="sql_client")

# Module-level connection pool — initialised once at worker startup
_pool: Optional[aioodbc.Pool] = None


async def get_pool() -> aioodbc.Pool:
    """Return (and lazily create) the module-level connection pool."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await aioodbc.create_pool(
            dsn=settings.azure_sql_connection_string,
            minsize=2,
            maxsize=10,
            autocommit=True,
        )
        logger.info("SQL connection pool initialised")
    return _pool


# ------------------------------------------------------------------
# Jobs table
# ------------------------------------------------------------------

async def upsert_job(job: dict) -> None:
    """Insert or update a job record (MERGE on correlation_id)."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    MERGE dbo.Jobs AS target
                    USING (SELECT ? AS correlation_id) AS source
                    ON target.correlation_id = source.correlation_id
                    WHEN MATCHED THEN UPDATE SET
                        status           = ?,
                        stage            = ?,
                        regather_count   = ?,
                        confidence_score = ?,
                        report_json      = ?,
                        score_json       = ?,
                        flags            = ?,
                        error_reason     = ?,
                        completed_at     = ?,
                        updated_at       = SYSUTCDATETIME()
                    WHEN NOT MATCHED THEN INSERT (
                        correlation_id, status, stage, prompt, depth,
                        regather_count, flags, original_message,
                        callback_url, submitted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    # WHEN MATCHED params
                    job["correlation_id"],
                    job.get("status", "queued"),
                    job.get("stage", "planner"),
                    job.get("regather_count", 0),
                    job.get("confidence_score"),
                    json.dumps(job["report"]) if job.get("report") else None,
                    json.dumps(job["score"]) if job.get("score") else None,
                    json.dumps(job.get("flags", [])),
                    job.get("error_reason"),
                    job.get("completed_at"),
                    # WHEN NOT MATCHED params
                    job["correlation_id"],
                    job.get("status", "queued"),
                    job.get("stage", "planner"),
                    job.get("prompt", ""),
                    job.get("depth", "standard"),
                    job.get("regather_count", 0),
                    json.dumps(job.get("flags", [])),
                    json.dumps(job.get("original_message", {})),
                    job.get("callback_url"),
                    job.get(
                        "submitted_at",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
    except Exception as exc:
        raise PersistenceError(
            f"upsert_job failed: {exc}",
            correlation_id=job.get("correlation_id"),
        ) from exc


async def get_job(correlation_id: str) -> Optional[dict]:
    """Fetch a single job record by correlation_id."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM dbo.Jobs WHERE correlation_id = ?",
                    correlation_id,
                )
                row = await cur.fetchone()
                if not row:
                    return None
                columns = [col[0] for col in cur.description]
                record = dict(zip(columns, row))
                # Deserialise JSON columns
                for col in (
                    "report_json",
                    "score_json",
                    "flags",
                    "original_message",
                ):
                    if record.get(col):
                        record[col] = json.loads(record[col])
                return record
    except Exception as exc:
        raise PersistenceError(
            f"get_job failed: {exc}",
            correlation_id=correlation_id,
        ) from exc


async def update_job_status(
    correlation_id: str,
    status: str,
    **extra_fields,
) -> None:
    """Lightweight status-only update for mid-pipeline transitions."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                set_clauses = ["status = ?", "updated_at = SYSUTCDATETIME()"]
                params: list = [status]

                for field in (
                    "stage",
                    "regather_count",
                    "confidence_score",
                    "error_reason",
                    "completed_at",
                ):
                    if field in extra_fields:
                        set_clauses.append(f"{field} = ?")
                        params.append(extra_fields[field])

                params.append(correlation_id)
                await cur.execute(
                    f"UPDATE dbo.Jobs SET {', '.join(set_clauses)} "
                    "WHERE correlation_id = ?",
                    *params,
                )
    except Exception as exc:
        raise PersistenceError(
            f"update_job_status failed: {exc}",
            correlation_id=correlation_id,
        ) from exc


# ------------------------------------------------------------------
# Facts table
# ------------------------------------------------------------------

async def insert_facts(correlation_id: str, facts: list[dict]) -> None:
    """Bulk-insert extracted facts. Replaces existing facts for the run."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM dbo.Facts WHERE correlation_id = ?",
                    correlation_id,
                )
                if not facts:
                    return
                rows = [
                    (
                        correlation_id,
                        f["claim"],
                        f["source_ref"],
                        f["source_type"],
                        f["topic_tag"],
                        f["confidence_raw"],
                        f.get("date"),
                    )
                    for f in facts
                ]
                await cur.executemany(
                    """
                    INSERT INTO dbo.Facts
                        (correlation_id, claim, source_ref, source_type,
                         topic_tag, confidence_raw, published_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
    except Exception as exc:
        raise PersistenceError(
            f"insert_facts failed: {exc}",
            correlation_id=correlation_id,
        ) from exc


async def get_facts(correlation_id: str) -> list[dict]:
    """Fetch all facts for a job."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM dbo.Facts WHERE correlation_id = ? ORDER BY id",
                    correlation_id,
                )
                columns = [col[0] for col in cur.description]
                return [dict(zip(columns, row)) async for row in cur]
    except Exception as exc:
        raise PersistenceError(
            f"get_facts failed: {exc}",
            correlation_id=correlation_id,
        ) from exc


# ------------------------------------------------------------------
# ConflictPoints table
# ------------------------------------------------------------------

async def insert_conflicts(
    correlation_id: str, conflicts: list[dict]
) -> None:
    """Bulk-insert conflict points. Replaces existing for the run."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM dbo.ConflictPoints WHERE correlation_id = ?",
                    correlation_id,
                )
                if not conflicts:
                    return
                rows = []
                for c in conflicts:
                    # Handle both nested (new) and flat (old) schemas
                    if isinstance(c.get("claim_a"), dict):
                        claim_a = c["claim_a"]["text"]
                        source_a = c["claim_a"].get("source_label", "")
                        claim_b = c["claim_b"]["text"]
                        source_b = c["claim_b"].get("source_label", "")
                    else:
                        claim_a = c.get("claim_a", "")
                        source_a = c.get("source_a", "")
                        claim_b = c.get("claim_b", "")
                        source_b = c.get("source_b", "")
                    rows.append((
                        correlation_id,
                        c["topic"],
                        claim_a,
                        source_a,
                        claim_b,
                        source_b,
                        c.get("severity", "minor"),
                    ))
                await cur.executemany(
                    """
                    INSERT INTO dbo.ConflictPoints
                        (correlation_id, topic, claim_a, source_a,
                         claim_b, source_b, severity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
    except Exception as exc:
        raise PersistenceError(
            f"insert_conflicts failed: {exc}",
            correlation_id=correlation_id,
        ) from exc


# ------------------------------------------------------------------
# Citations table
# ------------------------------------------------------------------

async def insert_citations(
    correlation_id: str, citations: list[dict]
) -> None:
    """Bulk-insert citations. Replaces existing for the run."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM dbo.Citations WHERE correlation_id = ?",
                    correlation_id,
                )
                if not citations:
                    return
                rows = [
                    (
                        correlation_id,
                        c["ref_id"],
                        c["url"],
                        c.get("title", ""),
                        c["source_type"],
                    )
                    for c in citations
                ]
                await cur.executemany(
                    """
                    INSERT INTO dbo.Citations
                        (correlation_id, ref_id, url, title, source_type)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )
    except Exception as exc:
        raise PersistenceError(
            f"insert_citations failed: {exc}",
            correlation_id=correlation_id,
        ) from exc


# ------------------------------------------------------------------
# Final output persistence (transactional)
# ------------------------------------------------------------------

async def persist_final_output(state: dict) -> None:
    """
    Write the completed job to all four SQL tables in a single transaction.
    Called once by the worker after workflow.run_async() returns.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            conn.autocommit = False
            try:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE dbo.Jobs SET
                            status           = 'complete',
                            stage            = 'complete',
                            confidence_score = ?,
                            report_json      = ?,
                            score_json       = ?,
                            flags            = ?,
                            completed_at     = SYSUTCDATETIME(),
                            updated_at       = SYSUTCDATETIME()
                        WHERE correlation_id = ?
                        """,
                        state["confidence_score"],
                        json.dumps(state["report"]),
                        json.dumps(state["score"]),
                        json.dumps(state.get("flags", [])),
                        state["correlation_id"],
                    )

                await insert_facts(
                    state["correlation_id"], state["extracted_facts"]
                )
                await insert_conflicts(
                    state["correlation_id"],
                    state["comparison_result"].get("conflicts",
                        state["comparison_result"].get("conflicted_points", [])),
                )
                await insert_citations(
                    state["correlation_id"],
                    state["report"].get("citations", []),
                )

                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                conn.autocommit = True
    except Exception as exc:
        raise PersistenceError(
            f"persist_final_output failed: {exc}",
            correlation_id=state.get("correlation_id"),
        ) from exc
