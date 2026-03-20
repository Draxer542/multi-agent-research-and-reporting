-- ============================================================
-- Research Agent — Baseline DDL
-- Version: V1
-- Target: Azure SQL Server (dbo schema)
-- ============================================================

-- ============================================================
-- Table: dbo.Jobs
-- One row per pipeline run. report_json and score_json store
-- the final structured outputs as SQL Server JSON columns.
-- ============================================================
CREATE TABLE dbo.Jobs (
    correlation_id      NVARCHAR(36)    NOT NULL PRIMARY KEY,
    status              NVARCHAR(50)    NOT NULL DEFAULT 'queued',
    stage               NVARCHAR(50)    NOT NULL DEFAULT 'planner',
    prompt              NVARCHAR(MAX)   NOT NULL,
    depth               NVARCHAR(20)    NOT NULL DEFAULT 'standard',
    regather_count      INT             NOT NULL DEFAULT 0,
    confidence_score    FLOAT           NULL,
    report_json         NVARCHAR(MAX)   NULL,
    score_json          NVARCHAR(MAX)   NULL,
    flags               NVARCHAR(MAX)   NULL,
    error_reason        NVARCHAR(MAX)   NULL,
    original_message    NVARCHAR(MAX)   NULL,
    callback_url        NVARCHAR(2048)  NULL,
    submitted_at        DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    completed_at        DATETIME2       NULL,
    updated_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT chk_jobs_status CHECK (status IN (
        'queued','processing','planned','gathered','extracted',
        'compared','written','scored','complete','failed','dead_lettered'
    )),
    CONSTRAINT chk_jobs_depth CHECK (depth IN ('quick','standard','deep'))
);

CREATE INDEX idx_jobs_status    ON dbo.Jobs (status);
CREATE INDEX idx_jobs_submitted ON dbo.Jobs (submitted_at DESC);

-- ============================================================
-- Table: dbo.Facts
-- One row per extracted fact. Linked to Jobs by correlation_id.
-- ============================================================
CREATE TABLE dbo.Facts (
    id                  INT             NOT NULL IDENTITY(1,1) PRIMARY KEY,
    correlation_id      NVARCHAR(36)    NOT NULL,
    claim               NVARCHAR(MAX)   NOT NULL,
    source_ref          NVARCHAR(2048)  NOT NULL,
    source_type         NVARCHAR(20)    NOT NULL,
    topic_tag           NVARCHAR(512)   NOT NULL,
    confidence_raw      FLOAT           NOT NULL,
    published_date      NVARCHAR(50)    NULL,
    created_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT fk_facts_jobs FOREIGN KEY (correlation_id)
        REFERENCES dbo.Jobs (correlation_id) ON DELETE CASCADE,
    CONSTRAINT chk_facts_source_type CHECK (source_type IN ('web','internal'))
);

CREATE INDEX idx_facts_correlation ON dbo.Facts (correlation_id);
CREATE INDEX idx_facts_topic       ON dbo.Facts (correlation_id, topic_tag);

-- ============================================================
-- Table: dbo.ConflictPoints
-- One row per identified conflict from the Comparator stage.
-- ============================================================
CREATE TABLE dbo.ConflictPoints (
    id                  INT             NOT NULL IDENTITY(1,1) PRIMARY KEY,
    correlation_id      NVARCHAR(36)    NOT NULL,
    topic               NVARCHAR(512)   NOT NULL,
    claim_a             NVARCHAR(MAX)   NOT NULL,
    source_a            NVARCHAR(2048)  NOT NULL,
    claim_b             NVARCHAR(MAX)   NOT NULL,
    source_b            NVARCHAR(2048)  NOT NULL,
    severity            NVARCHAR(10)    NOT NULL DEFAULT 'minor',
    created_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT fk_conflicts_jobs FOREIGN KEY (correlation_id)
        REFERENCES dbo.Jobs (correlation_id) ON DELETE CASCADE,
    CONSTRAINT chk_conflicts_severity CHECK (severity IN ('minor','major'))
);

CREATE INDEX idx_conflicts_correlation ON dbo.ConflictPoints (correlation_id);

-- ============================================================
-- Table: dbo.Citations
-- One row per citation in the final report.
-- ============================================================
CREATE TABLE dbo.Citations (
    id                  INT             NOT NULL IDENTITY(1,1) PRIMARY KEY,
    correlation_id      NVARCHAR(36)    NOT NULL,
    ref_id              NVARCHAR(50)    NOT NULL,
    url                 NVARCHAR(2048)  NOT NULL,
    title               NVARCHAR(1024)  NULL,
    source_type         NVARCHAR(20)    NOT NULL,
    accessed_at         DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT fk_citations_jobs FOREIGN KEY (correlation_id)
        REFERENCES dbo.Jobs (correlation_id) ON DELETE CASCADE
);

CREATE INDEX idx_citations_correlation ON dbo.Citations (correlation_id);
