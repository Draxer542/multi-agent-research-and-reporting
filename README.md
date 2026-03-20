# Multi-Agent Research & Reporting Platform

A production-grade, six-stage AI pipeline that accepts a natural-language research prompt, autonomously gathers evidence from the open web **and** an internal document corpus, cross-references sources for agreements and conflicts, and produces a fully cited, structured JSON report with confidence scoring.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Pipeline Stages](#pipeline-stages)
3. [Tech Stack](#tech-stack)
4. [Project Structure](#project-structure)
5. [Getting Started](#getting-started)
6. [Environment Variables](#environment-variables)
7. [API Reference](#api-reference)
8. [Frontend Dashboard](#frontend-dashboard)
9. [Security & Safety](#security--safety)
10. [Persistence Layer](#persistence-layer)
11. [Monitoring & Observability](#monitoring--observability)
12. [Evaluation Harness](#evaluation-harness)
13. [Docker Deployment](#docker-deployment)
14. [Report Schema](#report-schema)

---

## Architecture Overview

```
                        ┌─────────────────────────────────┐
                        │     FastAPI  (api/main.py)       │
                        │   POST /research → enqueue job   │
                        │   GET  /research/{id} → poll     │
                        └────────────┬────────────────────┘
                                     │  Azure Queue Storage
                                     ▼
              ┌──────────────────────────────────────────────┐
              │           Queue Worker (background task)      │
              │         worker/queue_worker.py                │
              └────────────┬─────────────────────────────────┘
                           │
          ┌────────────────▼────────────────┐
          │  MAF Workflow (WorkflowBuilder)  │
          │  pipeline/workflow.py            │
          └────────────────┬────────────────┘
                           │
    ┌──────────────────────▼──────────────────────┐
    │                                              │
    │  [1] Planner ─► [2] Gatherer ─► [3] Extractor│
    │                   ▲                 │        │
    │                   │            [4] Comparator │
    │                   │              /       \    │
    │             (re-gather       (proceed) (low   │
    │              if < 0.55)        │     score)   │
    │                   └────────────┘        │    │
    │                                         │    │
    │  [5] Writer ◄───────────────────────────┘    │
    │       │                                      │
    │  [6] Scorer ─► Persist + Blob Archive        │
    │                                              │
    └──────────────────────────────────────────────┘
```

The pipeline is built on the **Microsoft Agent Framework (MAF)** `WorkflowBuilder`, which defines a directed graph of executor functions with conditional edges for re-gather branching.

---

## Pipeline Stages

### Stage 1 — Planner (`pipeline/executors/planner.py`)

Decomposes the user's research prompt into **3–5 focused sub-queries** using Azure OpenAI. Outputs a `TaskPlan` containing:

| Field | Description |
|---|---|
| `sub_queries` | List of targeted search queries |
| `source_types` | Suggested source channels (`web`, `internal`, `both`) |
| `scope_notes` | Contextual notes for downstream agents |
| `estimated_complexity` | `low` / `medium` / `high` |

### Stage 2 — Source Gatherer (`pipeline/executors/gatherer.py`)

Executes all sub-queries in parallel against **two data sources** simultaneously:

- **Tavily API** (`tools/tavily_search.py`) — live web search with relevance ranking
- **Azure AI Search** (`tools/azure_ai_search.py`) — semantic search over your internal document index

Both sources are **always queried** for every sub-query regardless of the planner's `source_types` hint, ensuring internal documents are never missed.

Results are:
1. **Deduplicated** by URL
2. **Cleaned** — web content passes through `clean_web_content()` to strip ads, newsletter blocks, cookie notices, and sidebar noise; internal docs get lighter `clean_internal_content()` cleanup
3. **Archived** to Azure Blob Storage as `{correlation_id}/raw_sources.json`

### Stage 3 — Extractor (`pipeline/executors/extractor.py`)

Iterates over each raw source and calls Azure OpenAI to extract **structured, citable facts** using Pydantic schema enforcement (`ExtractionResult`). Each `ExtractedFact` contains:

| Field | Description |
|---|---|
| `claim` | The factual claim text |
| `source_ref` | URL or doc ID of the source |
| `source_type` | `web` or `internal` |
| `date` | Optional date of the claim |
| `confidence_raw` | 0.0–1.0 confidence estimate |
| `topic_tag` | Most relevant sub-query topic |

Includes a **self-correction retry** — if the LLM response fails Pydantic validation, a correction prompt is sent with the validation error.

### Stage 4 — Comparator (`pipeline/executors/comparator.py`)

Groups facts by topic and identifies:

- **Agreements** — claims supported by multiple sources, with strength levels (`strong` / `moderate` / `weak`)
- **Conflicts** — contradictory claims between sources, with severity (`minor` / `major`), resolution status, and analyst notes
- **Open questions** — gaps identified during comparison
- **Confidence score** — 0.0–1.0 composite score

**Conditional branching:** If the confidence score falls below **0.55**, the workflow loops back to the Gatherer for a re-gather cycle (max 2 retries, controlled by `pipeline/edges.py`).

### Stage 5 — Writer (`pipeline/executors/writer.py`)

Produces the final structured research report in a rich JSON schema. The writer receives extracted facts, comparison analysis, and source metadata, and generates:

- **Report metadata** (title, ID, timestamps)
- **Executive summary** with key takeaways
- **Findings** with per-finding confidence, source agreement status, supporting facts, and citation references
- **Agreements** with strength indicators and supporting citations
- **Conflicts** with claim-by-claim comparison, severity, resolution status, and analyst notes
- **Open questions** with priority and origin classification
- **Citations** with full bibliographic data (authors, venue, year, source type, relevance score)
- **Methodology note**

### Stage 6 — Confidence Scorer (`pipeline/executors/scorer.py`)

Applies **deterministic heuristics** to compute the final quality score:

| Heuristic | Weight |
|---|---|
| Source diversity (unique domains) | Coverage breadth |
| Sub-query coverage ratio | Completeness |
| Conflict ratio | Lower is better |
| Average fact confidence | Raw quality |

Outputs a composite `overall_confidence` score and a `recommendation` (`publish` / `review` / `insufficient`). The final report + score are persisted to both **Azure SQL** and **Azure Blob Storage** (`Reports/{correlation_id}/report.json`).

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Orchestration | Microsoft Agent Framework (MAF) | Workflow graph with conditional edges |
| LLM | Azure AI Foundry (GPT-4o) | Planning, extraction, comparison, report writing |
| API | FastAPI + Uvicorn | HTTP trigger and job polling |
| Queue | Azure Queue Storage | Async job dispatch |
| Relational DB | Azure SQL Server (via `aioodbc`) | Job tracking, facts, conflicts, citations |
| Object Storage | Azure Blob Storage | Raw source archives and final reports |
| Document Search | Azure AI Search (semantic) | Internal document corpus retrieval |
| Web Search | Tavily API | Live web search |
| Monitoring | OpenTelemetry + Azure Application Insights | Traces, metrics, logs |
| Schema Enforcement | Pydantic v2 | Request/response validation, LLM output parsing |
| Frontend | Vanilla HTML/CSS/JS | Dashboard with live feed and interactive report |

---

## Project Structure

```
multi-agent-research-and-reporting/
│
├── api/                           # FastAPI application layer
│   ├── main.py                    # App instance, routes, startup (auto-starts worker)
│   ├── models.py                  # Pydantic request/response schemas
│   └── queue_client.py            # Azure Queue Storage publisher
│
├── pipeline/                      # MAF workflow & state
│   ├── workflow.py                # WorkflowBuilder graph definition
│   ├── state.py                   # ResearchState dataclass (flows between stages)
│   ├── edges.py                   # Conditional edge functions (regather vs. write)
│   └── executors/                 # One executor per pipeline stage
│       ├── planner.py             # [1] Prompt decomposition into sub-queries
│       ├── gatherer.py            # [2] Parallel web + internal doc fetch
│       ├── extractor.py           # [3] Structured fact extraction per source
│       ├── comparator.py          # [4] Agreement/conflict analysis + confidence
│       ├── writer.py              # [5] Rich structured report generation
│       └── scorer.py              # [6] Deterministic quality scoring + archival
│
├── tools/                         # External tool wrappers
│   ├── tavily_search.py           # Tavily web search API client (with retry)
│   ├── azure_ai_search.py         # Azure AI Search semantic retrieval (with retry)
│   ├── content_cleaner.py         # Web/internal content noise removal
│   ├── injection_filter.py        # Prompt injection pattern detection
│   └── scope_guard.py             # LLM-based content scope validation
│
├── persistence/                   # Data persistence layer
│   ├── sql_client.py              # Azure SQL async client (Jobs, Facts, Conflicts, Citations)
│   ├── blob_client.py             # Azure Blob Storage (raw sources + reports)
│   ├── schemas.py                 # SQL DDL migration runner
│   └── search_index.py            # Azure AI Search index configuration
│
├── worker/                        # Background job processor
│   └── queue_worker.py            # Queue consumer loop (auto-started with API)
│
├── monitoring/                    # Observability
│   └── telemetry.py               # OpenTelemetry + Application Insights setup
│
├── core/                          # Shared infrastructure
│   ├── config.py                  # Pydantic-settings environment configuration
│   ├── logging.py                 # Structured logger with correlation ID support
│   ├── exceptions.py              # Hierarchical exception classes
│   └── openai_client.py           # Azure OpenAI async client factory
│
├── evaluation/                    # Quality assurance
│   ├── harness.py                 # Evaluation scoring harness (pytest-compatible)
│   └── fixtures/
│       └── fixtures.py            # Test fixture definitions with expected thresholds
│
├── static/                        # Frontend dashboard
│   ├── index.html                 # Landing page + dashboard layout
│   ├── app.js                     # Client-side logic, polling, report rendering
│   └── styles.css                 # Design system (Material-inspired)
│
├── infra/                         # Infrastructure-as-Code (Azure Bicep)
├── tests/                         # Unit tests
│   └── test_pipeline.py
│
├── research_papers/               # Reference schemas and sample data
│   ├── report_structure.json      # Report template schema
│   └── blob_content.json          # Sample blob content
│
├── Dockerfile                     # API service container
├── Dockerfile.worker              # Worker service container
├── requirements.txt               # Python dependencies
├── plan.md                        # Full implementation blueprint
└── .env                           # Environment variables (not committed)
```

---

## Getting Started

### Prerequisites

- **Python 3.11+**
- **ODBC Driver 18 for SQL Server** ([install guide](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- An **Azure subscription** with the following resources provisioned:
  - Azure AI Foundry (OpenAI GPT-4o deployment)
  - Azure Storage Account (Queue + Blob)
  - Azure SQL Server + database
  - Azure AI Search service + index
  - Azure Application Insights (optional, for monitoring)
- A **Tavily API key** ([tavily.com](https://tavily.com))

### Installation

```bash
# Clone the repository
git clone https://github.com/Draxer542/multi-agent-research-and-reporting.git
cd multi-agent-research-and-reporting

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### Database Setup

Run the SQL migrations to create the required tables (Jobs, Facts, ConflictPoints, Citations):

```bash
python -m persistence.schemas
```

### Running the Application

```bash
# Single command — starts both API server and background worker
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The queue worker is **automatically started** as a background `asyncio.Task` when the API server boots — no separate process needed.

Open the dashboard at **http://127.0.0.1:8000**.

---

## Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# ── Azure OpenAI ──────────────────────────────────────────
AZURE_OPENAI_ENDPOINT=https://<your-endpoint>.cognitiveservices.azure.com/
AZURE_OPENAI_API_KEY=<your-api-key>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-12-01-preview

# ── Azure Storage (Queue + Blob) ─────────────────────────
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_STORAGE_QUEUE_NAME=research-jobs

# ── Azure SQL Server ─────────────────────────────────────
AZURE_SQL_CONNECTION_STRING=DRIVER={ODBC Driver 18 for SQL Server};SERVER=...

# ── Azure AI Search ──────────────────────────────────────
AZURE_SEARCH_ENDPOINT=https://<your-search>.search.windows.net
AZURE_SEARCH_KEY=<your-admin-key>
AZURE_SEARCH_INDEX=internal-docs

# ── Tavily ───────────────────────────────────────────────
TAVILY_API_KEY=tvly-...

# ── Application Insights (optional) ──────────────────────
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...

# ── Logging ──────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_FORMAT=console
```

All variables are validated at startup by `core/config.py` using `pydantic-settings`.

---

## API Reference

### `POST /research`

Submit a new research job for asynchronous processing.

**Request body:**

```json
{
  "prompt": "Compare transformer architectures for NLP tasks",
  "depth": "standard",
  "source_hints": ["web", "internal"],
  "callback_url": "https://your-webhook.com/notify"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string (10–2000 chars) | ✅ | The research question |
| `depth` | `quick` \| `standard` \| `deep` | ❌ | Controls thoroughness (default: `standard`) |
| `source_hints` | string[] | ❌ | Preferred source channels |
| `callback_url` | string | ❌ | Webhook URL for completion notification |

**Response (202 Accepted):**

```json
{
  "correlation_id": "a1b2c3d4-...",
  "status": "queued",
  "poll_url": "/research/a1b2c3d4-..."
}
```

### `GET /research/{correlation_id}`

Poll the current status of a research job.

**Response fields:**

| Field | Description |
|---|---|
| `status` | `queued` → `processing` → `complete` / `failed` |
| `stage` | Current pipeline stage (`planner`, `gatherer`, `extractor`, `comparator`, `writer`, `scorer`) |
| `report_json` | Full structured report (when complete) |
| `score_json` | Confidence score breakdown (when complete) |
| `confidence_score` | Overall confidence (0.0–1.0) |
| `error_reason` | Error description (if failed) |

### `POST /replay/{correlation_id}`

Re-enqueue a failed or dead-lettered job using its original payload. Only available for jobs with status `failed` or `dead_lettered`.

### `GET /health`

Health check endpoint. Returns `{"status": "ok"}`.

---

## Frontend Dashboard

The application includes a built-in single-page dashboard served from `/static/`:

### Landing Page
- Hero section with CTA to enter the research dashboard
- "How It Works" step cards explaining the pipeline

### Research Dashboard
- **Prompt input** — submit a research question with Enter or button click
- **Intelligence Feed** (left panel) — real-time stage transitions showing which agent is active (Planner → Gatherer → Extractor → Comparator → Writer → Scorer)
- **Live Report** (right panel) — renders the full structured report with:
  - Confidence badge with color-coded recommendation
  - Executive summary with key takeaways
  - Finding cards with confidence scores and source agreement tags
  - Supporting facts with inline **citation pills**
  - Agreement cards with strength indicators
  - Conflict cards with claim comparison layout and analyst notes
  - Open questions with priority tags
  - Full reference list

### Citation Pills

Citations are rendered as compact interactive pills (e.g. `🌐 ref-1` or `📄 ref-2`). Hovering over a pill expands a floating tooltip showing:
- Full source title
- Authors and publication year
- Venue
- Clickable URL link

---

## Security & Safety

### Prompt Injection Filter (`tools/injection_filter.py`)

All incoming prompts pass through a regex-based filter that detects common injection patterns:

- `"ignore all previous instructions"`
- `"you are now"` / `"act as"`
- `"jailbreak"` / `"DAN mode"`
- Common injection delimiters (`<|...|>`, `[INST]`)
- Role override markers (`### System`, `### Human`)

Matched prompts are rejected with **HTTP 400** before reaching the pipeline.

### Content Scope Guard (`tools/scope_guard.py`)

A lightweight LLM pre-check evaluates whether the prompt falls within acceptable business research scope. Rejects prompts requesting:
- Personal data lookup or surveillance
- Harmful, violent, or illegal content
- Specific legal or medical advice
- Content completely unrelated to research

The guard is **permissive by default** — it errs on the side of allowing prompts and fails-open if the LLM call itself errors.

---

## Persistence Layer

### Azure SQL Server (`persistence/sql_client.py`)

Uses `aioodbc` async connection pooling (pool size 2–10). Four tables:

| Table | Purpose |
|---|---|
| `dbo.Jobs` | Job lifecycle tracking (status, stage, report JSON, score JSON, timestamps) |
| `dbo.Facts` | Extracted facts per job (claim, source_ref, confidence, topic_tag) |
| `dbo.ConflictPoints` | Recorded conflicts (claim_a, claim_b, severity, resolution_status) |
| `dbo.Citations` | Full citation records (ref_id, title, authors, venue, URL) |

Key operations:
- `upsert_job()` — **MERGE** (insert or update) on `correlation_id`
- `update_job_status()` — lightweight status/stage update
- `persist_final_output()` — atomically write completed job data + all related tables
- `get_job()` — retrieve full job record for polling

### Azure Blob Storage (`persistence/blob_client.py`)

Two storage paths:

| Path | Content |
|---|---|
| `{correlation_id}/raw_sources.json` | Cleaned raw source documents from the gatherer |
| `Reports/{correlation_id}/report.json` | Final structured report + score metadata |

### SQL Migrations (`persistence/schemas.py`)

DDL scripts in `persistence/migrations/` are executed with `python -m persistence.schemas`. Scripts follow version naming: `V1__description.sql`, `V2__...`, etc.

---

## Monitoring & Observability

### OpenTelemetry + Application Insights (`monitoring/telemetry.py`)

Uses `azure-monitor-opentelemetry` for automatic mapping to Application Insights tables:

| Signal | Application Insights Table |
|---|---|
| FastAPI HTTP requests | `requests` |
| Outgoing httpx calls | `dependencies` |
| Unhandled exceptions | `exceptions` |
| Python logging | `traces` |
| Custom pipeline metrics | `customMetrics` |

### Custom Metrics

| Metric | Type | Description |
|---|---|---|
| `research.jobs.completed` | Counter | Jobs completed (by status) |
| `research.jobs.duration_seconds` | Histogram | End-to-end job duration |
| `research.confidence_score` | Histogram | Final confidence distribution |
| `research.regather.count` | Counter | Re-gather cycles triggered |
| `research.errors` | Counter | Errors by stage |
| `research.deadletter.count` | Counter | Dead-lettered messages |

Each pipeline stage is also instrumented with OpenTelemetry spans via the `timed_stage()` context manager.

---

## Evaluation Harness

The project includes a pytest-compatible evaluation framework (`evaluation/harness.py`) for validating pipeline output quality.

### Running Evaluations

```bash
pytest evaluation/harness.py -v
```

### How It Works

Test fixtures in `evaluation/fixtures/fixtures.py` define:
- A research prompt
- Expected quality thresholds (min facts, min sources, min confidence, must-have citations/conflicts)

The harness runs each fixture through the full pipeline and scores the output:

| Check | Validates |
|---|---|
| `schema_valid` | Report contains all required top-level keys |
| `min_fact_count` | Minimum number of extracted facts |
| `min_source_count` | Minimum unique source references |
| `confidence_score_ok` | Score ≥ threshold |
| `has_citations` | Citations present when required |
| `has_conflicts` | Conflicts present when expected |

---

## Docker Deployment

Two Dockerfiles are provided for containerised deployment:

### API Service (`Dockerfile`)

```bash
docker build -t research-api -f Dockerfile .
docker run -p 8000:8000 --env-file .env research-api
```

Runs the FastAPI server with the background worker on port 8000.

### Worker Service (`Dockerfile.worker`)

```bash
docker build -t research-worker -f Dockerfile.worker .
docker run --env-file .env research-worker
```

Runs the queue worker as a standalone process (useful for horizontal scaling).

Both images are based on `python:3.11-slim` and include **ODBC Driver 18 for SQL Server**.

---

## Report Schema

The final report follows a rich structured JSON schema. Here is a condensed overview:

```json
{
  "report_metadata": {
    "report_id": "RPT-<uuid>",
    "correlation_id": "<job-id>",
    "title": "Report title",
    "generated_at": "ISO 8601",
    "prompt": "original prompt",
    "depth": "standard"
  },
  "executive_summary": {
    "text": "150-250 word overview",
    "word_count": 200,
    "key_takeaways": ["...", "...", "..."]
  },
  "findings": [{
    "finding_id": "FND-001",
    "sub_query": "original sub-query",
    "summary": "one-sentence summary",
    "detail": "detailed paragraph",
    "confidence": 0.85,
    "source_agreement": "full | partial | contradicted",
    "citations": ["ref-1", "ref-2"],
    "supporting_facts": [{"claim": "...", "citation_ref": "ref-1"}]
  }],
  "agreements": [{
    "agreement_id": "AGR-001",
    "topic": "topic name",
    "statement": "what sources agree on",
    "supporting_citations": ["ref-1", "ref-2"],
    "strength": "strong | moderate | weak"
  }],
  "conflicts": [{
    "conflict_id": "CON-001",
    "topic": "topic name",
    "claim_a": {"text": "...", "citation_ref": "ref-1", "source_label": "Source A"},
    "claim_b": {"text": "...", "citation_ref": "ref-2", "source_label": "Source B"},
    "severity": "minor | major",
    "resolution_status": "unresolved | resolved_in_source | partially_resolved",
    "analyst_note": "analysis of the conflict"
  }],
  "open_questions": [{
    "question_id": "OQ-001",
    "question": "the unresolved question",
    "origin": "gap_in_coverage | conflict_unresolved | scope_limitation",
    "priority": "high | medium | low"
  }],
  "citations": [{
    "ref_id": "ref-1",
    "title": "Source Title",
    "authors": ["Author Name"],
    "venue": "Journal/Site",
    "publication_year": "2024",
    "source_type": "web | internal",
    "url": "https://...",
    "file_name": "document.pdf",
    "relevance_score": 0.92
  }],
  "methodology_note": "How sources were gathered and compared"
}
```

---

## Exception Hierarchy

All project-specific exceptions derive from `ResearchAgentError` and carry `correlation_id` + `stage` for structured error reporting:

```
ResearchAgentError
├── PipelineError
│   └── ExecutorError          (per-stage failures)
├── ToolError                  (Tavily, Azure AI Search)
├── PersistenceError           (SQL, Blob Storage)
├── QueueError                 (Queue publish/consume)
├── SchemaValidationError      (LLM response parsing)
├── PromptRejectedError        (injection filter / scope guard)
└── RetryExhaustedError        (all retry attempts failed)
```

---

## License

This project is proprietary. All rights reserved.
