# Multi-Agent Research & Reporting Platform

A multi-step, multi-agent pipeline that accepts a research prompt and produces a structured JSON report backed by cited sources, conflict analysis, and confidence scoring.

## Pipeline Overview

```
HTTP POST /research
    │
    ▼
Azure Queue Storage
    │
    ▼
[1] Planner Agent         → task breakdown, sub-queries
    │
    ▼
[2] Source Gatherer       → parallel fetch: Tavily API + Azure AI Search
    │
    ▼
[3] Extractor Agent       → structured fact extraction per source
    │
    ▼
[4] Comparator Agent      → agree/conflict analysis + confidence score
    │
    ├── score < 0.55 ────► Re-gather branch (loops back to Stage 2)
    │
    ▼
[5] Writer Agent          → structured report + open questions
    │
    ▼
[6] Confidence Scorer     → final composite score + flags
    │
    ▼
Persist to Azure SQL + Blob Storage → Final JSON
```

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | Microsoft Agent Framework Workflows |
| LLM | Azure AI Foundry (GPT-4o) |
| API | FastAPI |
| Queue | Azure Queue Storage |
| Persistence | Azure SQL Server + Azure Blob Storage |
| Search | Tavily API + Azure AI Search |
| Monitoring | OpenTelemetry + Azure Application Insights |

## Project Structure

```
├── api/                  # FastAPI trigger endpoint
├── pipeline/             # MAF workflow graph, state, edges
│   └── executors/        # One executor per pipeline stage
├── tools/                # External API wrappers (Tavily, AI Search)
├── persistence/          # Azure SQL + Blob Storage clients
│   └── migrations/       # SQL DDL scripts
├── worker/               # Queue consumer loop
├── monitoring/           # OpenTelemetry setup
├── evaluation/           # Test fixtures + scoring harness
├── core/                 # Shared logging, config, exceptions
├── infra/                # Azure Bicep IaC
└── tests/                # Unit tests
```

## Getting Started

### Prerequisites

- Python 3.11+
- Azure subscription with the required resources (see `plan.md` §4.1)
- ODBC Driver 18 for SQL Server

### Setup

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Configure environment
# Copy .env and fill in your Azure credentials

# Run API server
uvicorn api.main:app --reload

# Run worker (separate terminal)
python -m worker.queue_worker
```

## Documentation

- **[plan.md](plan.md)** — Full implementation blueprint with code samples and architecture details
