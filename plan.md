# Multi-Agent Research and Reporting System — Implementation Plan

**Version:** 1.0  
**Status:** Implementation Blueprint

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Repository and Project Structure](#3-repository-and-project-structure)
4. [Infrastructure Setup](#4-infrastructure-setup)
5. [Stage 1 — Trigger Layer](#5-stage-1--trigger-layer)
6. [Stage 2 — Planner Agent](#6-stage-2--planner-agent)
7. [Stage 3 — Source Gatherer](#7-stage-3--source-gatherer)
8. [Stage 4 — Extractor Agent](#8-stage-4--extractor-agent)
9. [Stage 5 — Comparator Agent](#9-stage-5--comparator-agent)
10. [Stage 6 — Writer Agent](#10-stage-6--writer-agent)
11. [Stage 7 — Confidence Scorer](#11-stage-7--confidence-scorer)
12. [Confidence-Aware Branching Logic](#12-confidence-aware-branching-logic)
13. [Persistence Layer](#13-persistence-layer)
14. [Cross-Step State Management](#14-cross-step-state-management)
15. [Error Handling and Dead-Letter Strategy](#15-error-handling-and-dead-letter-strategy)
16. [Final Output](#16-final-output)
17. [Monitoring and Tracing](#17-monitoring-and-tracing)
18. [Guardrails and Safety](#18-guardrails-and-safety)
19. [Evaluation Harness](#19-evaluation-harness)
20. [Deployment](#20-deployment)
21. [End-to-End Flow Summary](#21-end-to-end-flow-summary)

---

## 1. System Overview

This system accepts a research prompt (market trend, competitor comparison, vendor due diligence, or technical feasibility) and runs it through a multi-step, multi-agent pipeline that:

- Pulls information from two distinct sources (Internal documents or web pages)
- Highlights agreements vs. conflicts across sources
- Produces a structured JSON report with citations and open questions
- Persists all artifacts with full audit trail
- Handles failures gracefully with retry, dead-letter, and replay

### Pipeline Stages

```
HTTP Request
    │
    ▼
Azure Queue Storage (trigger)
    │
    ▼
[Stage 1] Planner Agent         → task breakdown, sub-queries
    │
    ▼
[Stage 2] Source Gatherer       → parallel fetch: Tavily API + Azure AI Search
    │
    ▼
[Stage 3] Extractor Agent       → structured fact extraction per source
    │
    ▼
[Stage 4] Comparator Agent      → agree/conflict analysis + confidence score
    │
    ├── score < 0.55 ──────────► Re-gather branch (loops back to Stage 2)
    │
    ▼
[Stage 5] Writer Agent          → structured report + open questions
    │
    ▼
[Stage 6] Confidence Scorer     → final composite score + flags
    │
    ▼
Persist to Azure SQL + Blob Storage
    │
    ▼
Final JSON response / callback notification
```

---

## 2. Technology Stack

| Layer               | Technology                                              | Purpose                                                                                  |
| ------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Orchestration       | Microsoft Agent Framework (`agent-framework`) Workflows | Graph-based multi-agent pipeline with typed executors, edges, and built-in checkpointing |
| Agent LLM           | Azure AI Foundry (GPT-4o via Azure OpenAI)              | All LLM calls across every agent role                                                    |
| Trigger             | FastAPI `POST /research`                                | HTTP ingestion, payload validation, correlation ID assignment                            |
| Queue               | Azure Queue Storage                                     | Async job dispatch; decouples HTTP layer from pipeline                                   |
| Dead-Letter         | Azure Queue Storage (separate `*-poison` queue)         | Failed messages after max dequeue count; replayable                                      |
| Web search tool     | Tavily Search API                                       | Live web content retrieval                                                               |
| Internal doc search | Azure AI Search (vector index)                          | RAG over internal document corpus                                                        |
| State persistence   | Azure SQL Server (via `aioodbc` + `pyodbc`)             | Job records, stage status, fact lists, final report — normalized relational schema       |
| Raw source archive  | Azure Blob Storage                                      | Raw fetched content keyed by `correlation_id`                                            |
| Schema enforcement  | Pydantic v2                                             | All LLM structured outputs and queue message payloads                                    |
| Monitoring          | Azure Application Insights + OpenTelemetry              | Distributed traces, per-stage latency, error rates                                       |
| Language            | Python 3.11                                             | Entire codebase                                                                          |

---

## 3. Repository and Project Structure

```
research-agent/
├── api/
│   ├── main.py                     # FastAPI app — trigger endpoint
│   ├── models.py                   # Pydantic request/response schemas
│   └── queue_client.py             # Azure Queue Storage publisher
│
├── pipeline/
│   ├── workflow.py                 # MAF WorkflowBuilder graph definition
│   ├── state.py                    # Shared ResearchState TypedDict
│   ├── executors/
│   │   ├── planner.py              # Stage 1: Planner executor
│   │   ├── gatherer.py             # Stage 2: Source Gatherer executor
│   │   ├── extractor.py            # Stage 3: Extractor executor
│   │   ├── comparator.py           # Stage 4: Comparator executor
│   │   ├── writer.py               # Stage 5: Writer executor
│   │   └── scorer.py               # Stage 6: Confidence Scorer executor
│   └── edges.py                    # Conditional edge definitions
│
├── tools/
│   ├── tavily_search.py            # Tavily API wrapper
│   ├── azure_ai_search.py          # Azure AI Search retriever
│   └── injection_filter.py         # Prompt injection pre-processor
│
├── persistence/
│   ├── sql_client.py               # Azure SQL read/write helpers
│   ├── blob_client.py              # Blob Storage raw archive helpers
│   ├── schemas.py                  # SQL table DDL and migration helpers
│   └── migrations/
│       └── V1__initial_schema.sql  # Baseline DDL
│
├── worker/
│   └── queue_worker.py             # Azure Queue Storage consumer loop
│
├── monitoring/
│   └── telemetry.py                # OpenTelemetry + App Insights setup
│
├── evaluation/
│   ├── fixtures/                   # Deterministic test prompts + expected outputs
│   └── harness.py                  # Scoring harness
│
├── infra/
│   ├── main.bicep                  # Azure infrastructure as code
│   └── parameters.json
│
├── tests/
│   └── test_pipeline.py
│
├── requirements.txt
└── README.md
```

---

## 4. Infrastructure Setup

### 4.1 Azure Resources Required

```
Resource Group: rg-research-agent-{env}

├── Azure Queue Storage Account       (sa-researchagent-{env})
│   ├── Queue: research-jobs          (main queue)
│   └── Queue: research-jobs-poison   (dead-letter — auto-created at max dequeue)
│
├── Azure SQL Server                  (sql-research-{env}.database.windows.net)
│   └── Database: ResearchAgentDB
│       ├── Table: dbo.Jobs           (one row per pipeline run)
│       ├── Table: dbo.Facts          (one row per extracted fact)
│       ├── Table: dbo.ConflictPoints (one row per identified conflict)
│       └── Table: dbo.Citations      (one row per citation)
│
├── Azure Blob Storage Account        (sa-rawsources-{env})
│   └── Container: raw-sources
│
├── Azure AI Foundry Project          (ai-research-{env})
│   └── Deployment: gpt-4o            (used by all agent roles)
│
├── Azure AI Search                   (srch-research-{env})
│   └── Index: internal-docs
│
├── Azure App Service / Container App (app-research-api-{env})
│   └── Hosts FastAPI trigger endpoint
│
└── Azure Application Insights        (appi-research-{env})
```

### 4.2 Environment Variables

```bash
# Logging
LOG_LEVEL=INFO
LOG_FORMAT=console                  # "console" (dev) | "json" (prod)

# Azure AI Foundry
AZURE_AI_PROJECT_ENDPOINT=https://<project>.api.azureml.ms
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_KEY=<key>                   # Optional — used when not using DefaultAzureCredential
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_ENDPOINT=<endpoint>             # Optional — direct OpenAI endpoint

# Azure Storage (unified — used by Queue and Blob)
AZURE_STORAGE_CONNECTION_STRING=<connection_string>
AZURE_STORAGE_ACCOUNT_NAME=saresearchagent
AZURE_STORAGE_QUEUE_NAME=research-jobs
AZURE_STORAGE_POISON_QUEUE_NAME=research-jobs-poison
BLOB_RAW_SOURCES_CONTAINER=raw-sources

# Azure SQL Server
AZURE_SQL_SERVER=sql-research-{env}.database.windows.net
AZURE_SQL_DATABASE=ResearchAgentDB
AZURE_SQL_USERNAME=researchagent-app
AZURE_SQL_PASSWORD=<password>
# Full ODBC connection string (used by aioodbc)
AZURE_SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=tcp:sql-research-{env}.database.windows.net,1433;Database=ResearchAgentDB;Uid=researchagent-app;Pwd=<password>;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;

# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<service>.search.windows.net
AZURE_SEARCH_KEY=<key>
AZURE_SEARCH_INDEX=internal-docs

# External tools
TAVILY_API_KEY=<key>

# Azure Monitor
APPLICATIONINSIGHTS_CONNECTION_STRING=<connection_string>
```

---

## 5. Stage 1 — Trigger Layer

### 5.1 FastAPI Endpoint

**File:** `api/main.py`

```python
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from api.models import ResearchRequest, ResearchAccepted
from api.queue_client import enqueue_job
from monitoring.telemetry import tracer

app = FastAPI(title="Research Agent API")

@app.post("/research", response_model=ResearchAccepted, status_code=202)
async def create_research_job(request: ResearchRequest):
    # Assign correlation ID
    correlation_id = str(uuid.uuid4())

    # Sanitize prompt against injection
    from tools.injection_filter import sanitize_prompt
    safe_prompt = sanitize_prompt(request.prompt)
    if safe_prompt is None:
        raise HTTPException(status_code=400, detail="Prompt failed safety check")

    # Build queue message
    job_message = {
        "correlation_id": correlation_id,
        "prompt": safe_prompt,
        "source_hints": request.source_hints,
        "depth": request.depth,
        "callback_url": request.callback_url,
        "submitted_at": datetime.utcnow().isoformat()
    }

    # Publish to Azure Queue Storage
    await enqueue_job(job_message)

    return ResearchAccepted(
        correlation_id=correlation_id,
        status="queued",
        poll_url=f"/research/{correlation_id}"
    )

@app.get("/research/{correlation_id}")
async def get_research_status(correlation_id: str):
    from persistence.sql_client import get_job
    job = await get_job(correlation_id)
    if not job:
        raise HTTPException(status_code=404)
    return job

@app.post("/replay/{correlation_id}")
async def replay_job(correlation_id: str):
    from persistence.sql_client import get_job
    job = await get_job(correlation_id)
    if not job or job["status"] not in ("failed", "dead_lettered"):
        raise HTTPException(status_code=400, detail="Job not eligible for replay")
    await enqueue_job(job["original_message"])
    return {"status": "requeued", "correlation_id": correlation_id}
```

### 5.2 Request/Response Schemas

**File:** `api/models.py`

```python
from pydantic import BaseModel, Field
from typing import Optional, List

class ResearchRequest(BaseModel):
    prompt: str = Field(..., min_length=10, max_length=2000)
    source_hints: Optional[List[str]] = None   # e.g. ["web", "internal"]
    depth: str = Field(default="standard")      # "quick" | "standard" | "deep"
    callback_url: Optional[str] = None

class ResearchAccepted(BaseModel):
    correlation_id: str
    status: str
    poll_url: str
```

### 5.3 Azure Queue Storage Publisher

**File:** `api/queue_client.py`

```python
import json
import base64
from azure.storage.queue.aio import QueueClient
from azure.identity.aio import DefaultAzureCredential
import os

async def enqueue_job(message: dict) -> None:
    credential = DefaultAzureCredential()
    queue_client = QueueClient(
        account_url=f"https://{os.environ['AZURE_STORAGE_ACCOUNT_NAME']}.queue.core.windows.net",
        queue_name=os.environ["AZURE_STORAGE_QUEUE_NAME"],
        credential=credential
    )
    encoded = base64.b64encode(json.dumps(message).encode()).decode()
    async with queue_client:
        await queue_client.send_message(encoded, visibility_timeout=30)
```

---

## 6. Stage 2 — Planner Agent

**File:** `pipeline/executors/planner.py`

The Planner receives the raw research prompt and produces a structured task plan: decomposed sub-queries, required source types, and scope constraints.

### 6.1 Executor Implementation

```python
from agent_framework import AIAgent, ExecutorContext
from pipeline.state import ResearchState
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from pydantic import BaseModel
from typing import List
import os, json

class TaskPlan(BaseModel):
    sub_queries: List[str]          # 3–5 focused sub-questions
    source_types: List[str]         # ["web", "internal", "both"]
    scope_notes: str                # Any constraints inferred from prompt
    estimated_complexity: str       # "low" | "medium" | "high"

PLANNER_SYSTEM_PROMPT = """
You are a research planning agent. Given a research prompt, decompose it into
3–5 focused sub-queries that together cover the topic comprehensively.
For each sub-query, determine whether it needs live web data, internal documents,
or both. Return ONLY valid JSON matching the TaskPlan schema.
Do not include explanations or markdown.
"""

async def planner_executor(context: ExecutorContext, state: ResearchState) -> ResearchState:
    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=credential
    )

    async with client:
        response = await client.inference.get_chat_completions(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Research prompt: {state['prompt']}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )

    raw = response.choices[0].message.content
    plan = TaskPlan.model_validate_json(raw)

    state["task_plan"] = plan.model_dump()
    state["status"] = "planned"
    state["stage"] = "gatherer"
    return state
```

---

## 7. Stage 3 — Source Gatherer

**File:** `pipeline/executors/gatherer.py`

Executes sub-queries in parallel against both sources. Results are tagged with source metadata and archived to Blob Storage.

### 7.1 Tool: Tavily Web Search

**File:** `tools/tavily_search.py`

```python
import httpx
import os
from typing import List, Dict

async def tavily_search(query: str, max_results: int = 5) -> List[Dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": os.environ["TAVILY_API_KEY"],
                "query": query,
                "search_depth": "advanced",
                "include_raw_content": True,
                "max_results": max_results
            },
            timeout=20
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {
                "source_type": "web",
                "url": r["url"],
                "title": r.get("title", ""),
                "content": r.get("raw_content") or r.get("content", ""),
                "score": r.get("score", 0)
            }
            for r in results
        ]
```

### 7.2 Tool: Azure AI Search (Internal Docs)

**File:** `tools/azure_ai_search.py`

```python
from azure.search.documents.aio import SearchClient
from azure.core.credentials import AzureKeyCredential
import os
from typing import List, Dict

async def internal_search(query: str, top: int = 5) -> List[Dict]:
    client = SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=os.environ["AZURE_SEARCH_INDEX"],
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"])
    )
    async with client:
        results = await client.search(
            search_text=query,
            top=top,
            query_type="semantic",
            semantic_configuration_name="default",
            select=["id", "title", "content", "source_url", "last_updated"]
        )
        docs = []
        async for r in results:
            docs.append({
                "source_type": "internal",
                "url": r.get("source_url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "doc_id": r.get("id", ""),
                "last_updated": r.get("last_updated", "")
            })
    return docs
```

### 7.3 Gatherer Executor

```python
import asyncio
from pipeline.state import ResearchState
from tools.tavily_search import tavily_search
from tools.azure_ai_search import internal_search
from persistence.blob_client import archive_sources
import json

async def gatherer_executor(context, state: ResearchState) -> ResearchState:
    plan = state["task_plan"]
    all_sources = []

    async def fetch_query(sub_query: str, source_types: list):
        tasks = []
        if "web" in source_types or "both" in source_types:
            tasks.append(tavily_search(sub_query))
        if "internal" in source_types or "both" in source_types:
            tasks.append(internal_search(sub_query))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged = []
        for r in results:
            if isinstance(r, Exception):
                continue          # logged separately via telemetry
            merged.extend(r)
        return merged

    # Parallel fetch across all sub-queries
    fetch_tasks = [
        fetch_query(sq, plan["source_types"])
        for sq in plan["sub_queries"]
    ]
    query_results = await asyncio.gather(*fetch_tasks)

    for batch in query_results:
        all_sources.extend(batch)

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for s in all_sources:
        if s["url"] not in seen_urls:
            seen_urls.add(s["url"])
            deduped.append(s)

    # Archive raw sources to Blob Storage
    await archive_sources(state["correlation_id"], deduped)

    state["raw_sources"] = deduped
    state["status"] = "gathered"
    state["stage"] = "extractor"
    return state
```

---

## 8. Stage 4 — Extractor Agent

**File:** `pipeline/executors/extractor.py`

Iterates over raw sources and extracts structured, citable facts using Azure AI Foundry with Pydantic schema enforcement.

### 8.1 Fact Schema

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class ExtractedFact(BaseModel):
    claim: str                          # The specific factual claim
    source_ref: str                     # URL or doc ID
    source_type: str                    # "web" | "internal"
    date: Optional[str] = None          # Publication/update date if available
    confidence_raw: float = Field(ge=0, le=1)   # Model's self-reported certainty
    topic_tag: str                      # Maps claim to a sub-query topic

class ExtractionResult(BaseModel):
    facts: List[ExtractedFact]
```

### 8.2 Extractor Executor

```python
EXTRACTOR_SYSTEM_PROMPT = """
You are a fact extraction agent. Given a source document and a list of sub-queries,
extract all distinct factual claims relevant to those sub-queries.
For each claim, record its source reference, an estimated confidence (0–1),
and tag it to the most relevant sub-query topic.
Return ONLY valid JSON matching the ExtractionResult schema. No markdown.
"""

async def extractor_executor(context, state: ResearchState) -> ResearchState:
    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=credential
    )

    all_facts = []

    async with client:
        for source in state["raw_sources"]:
            # Skip empty content
            if not source.get("content", "").strip():
                continue

            user_message = f"""
Sub-queries: {json.dumps(state['task_plan']['sub_queries'])}

Source URL: {source['url']}
Source type: {source['source_type']}
Content:
{source['content'][:4000]}
"""
            response = await client.inference.get_chat_completions(
                model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
                messages=[
                    {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )

            raw = response.choices[0].message.content
            result = ExtractionResult.model_validate_json(raw)
            all_facts.extend(result.facts)

    state["extracted_facts"] = [f.model_dump() for f in all_facts]
    state["status"] = "extracted"
    state["stage"] = "comparator"
    return state
```

---

## 9. Stage 5 — Comparator Agent

**File:** `pipeline/executors/comparator.py`

Groups facts by topic, identifies agreements and conflicts across sources, and computes a confidence score that drives the conditional branching edge.

### 9.1 Comparison Schema

```python
from pydantic import BaseModel
from typing import List

class ConflictPoint(BaseModel):
    topic: str
    claim_a: str
    source_a: str
    claim_b: str
    source_b: str
    severity: str           # "minor" | "major"

class ComparisonResult(BaseModel):
    agreed_points: List[str]
    conflicted_points: List[ConflictPoint]
    open_questions: List[str]          # Unresolved from conflict or low coverage
    confidence_score: float            # 0–1; drives routing edge
    coverage_ratio: float              # proportion of sub-queries with ≥2 source facts
```

### 9.2 Comparator Executor

```python
COMPARATOR_SYSTEM_PROMPT = """
You are a research comparator agent. Given a list of extracted facts from multiple sources,
identify points of agreement and conflict across sources for the same claims.
Compute a confidence_score (0–1) reflecting overall source agreement and coverage:
  - 1.0 = all sources agree, all sub-queries fully covered
  - 0.0 = major conflicts across all topics, sparse coverage
Also list open questions arising from unresolved conflicts or information gaps.
Return ONLY valid JSON matching the ComparisonResult schema. No markdown.
"""

async def comparator_executor(context, state: ResearchState) -> ResearchState:
    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=credential
    )

    async with client:
        response = await client.inference.get_chat_completions(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
            messages=[
                {"role": "system", "content": COMPARATOR_SYSTEM_PROMPT},
                {"role": "user", "content": f"""
Sub-queries: {json.dumps(state['task_plan']['sub_queries'])}
Extracted facts: {json.dumps(state['extracted_facts'])}
"""}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )

    raw = response.choices[0].message.content
    result = ComparisonResult.model_validate_json(raw)

    state["comparison_result"] = result.model_dump()
    state["confidence_score"] = result.confidence_score
    state["status"] = "compared"

    # Branch decision stored in state — read by conditional edge
    if result.confidence_score < 0.55:
        state["stage"] = "regather"
        state["regather_count"] = state.get("regather_count", 0) + 1
    else:
        state["stage"] = "writer"

    return state
```

---

## 10. Stage 6 — Writer Agent

**File:** `pipeline/executors/writer.py`

Produces the final structured research report including executive summary, per-topic findings, source agreements, conflicts, citations, and mandatory open questions.

### 10.1 Report Schema

```python
from pydantic import BaseModel
from typing import List, Optional

class Citation(BaseModel):
    ref_id: str
    url: str
    title: str
    source_type: str
    accessed_at: str

class Finding(BaseModel):
    sub_query: str
    summary: str
    supporting_citations: List[str]     # ref_ids
    confidence: float

class ResearchReport(BaseModel):
    title: str
    executive_summary: str
    findings: List[Finding]
    agreed_points: List[str]
    conflicted_points: List[dict]
    open_questions: List[str]
    citations: List[Citation]
    methodology_note: str
```

### 10.2 Writer Executor

```python
WRITER_SYSTEM_PROMPT = """
You are a research report writing agent. Given extracted facts, a comparison analysis,
and citation sources, produce a comprehensive structured research report.
Include an executive summary, per-topic findings with citation references,
a clear section on agreements and conflicts, and a mandatory open questions section
for unresolved issues or information gaps.
Return ONLY valid JSON matching the ResearchReport schema. No markdown.
"""

async def writer_executor(context, state: ResearchState) -> ResearchState:
    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=credential
    )

    async with client:
        response = await client.inference.get_chat_completions(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
            messages=[
                {"role": "system", "content": WRITER_SYSTEM_PROMPT},
                {"role": "user", "content": f"""
Prompt: {state['prompt']}
Task plan: {json.dumps(state['task_plan'])}
Extracted facts: {json.dumps(state['extracted_facts'])}
Comparison result: {json.dumps(state['comparison_result'])}
Source metadata: {json.dumps([{"url": s["url"], "title": s["title"], "source_type": s["source_type"]} for s in state["raw_sources"]])}
"""}
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=4000
        )

    raw = response.choices[0].message.content
    report = ResearchReport.model_validate_json(raw)

    state["report"] = report.model_dump()
    state["status"] = "written"
    state["stage"] = "scorer"
    return state
```

---

## 11. Stage 7 — Confidence Scorer

**File:** `pipeline/executors/scorer.py`

Applies a composite scoring pass combining deterministic heuristics with a lightweight LLM call to produce the final quality score attached to the output artifact.

### 11.1 Score Schema

```python
from pydantic import BaseModel
from typing import List

class ReportScore(BaseModel):
    overall_confidence: float       # 0–1 composite
    source_diversity: float         # proportion of unique domains
    coverage_score: float           # sub-queries with ≥2 citations / total sub-queries
    conflict_ratio: float           # conflicted topics / total topics
    flags: List[str]                # e.g. ["low_source_count", "unresolved_major_conflict"]
    recommendation: str             # "publish" | "review" | "reject"
```

### 11.2 Scorer Executor

```python
async def scorer_executor(context, state: ResearchState) -> ResearchState:
    facts = state["extracted_facts"]
    report = state["report"]
    comparison = state["comparison_result"]
    sub_queries = state["task_plan"]["sub_queries"]

    # Deterministic heuristics
    unique_domains = len(set(
        f["source_ref"].split("/")[2] if f["source_ref"].startswith("http") else "internal"
        for f in facts
    ))
    total_possible_domains = len(facts)
    source_diversity = min(unique_domains / max(total_possible_domains, 1), 1.0)

    topics_with_multi = sum(
        1 for sq in sub_queries
        if sum(1 for f in facts if f["topic_tag"] == sq) >= 2
    )
    coverage_score = topics_with_multi / max(len(sub_queries), 1)

    conflict_ratio = len(comparison["conflicted_points"]) / max(
        len(comparison["agreed_points"]) + len(comparison["conflicted_points"]), 1
    )

    flags = []
    if len(facts) < 5:
        flags.append("low_fact_count")
    if unique_domains < 2:
        flags.append("single_source_domain")
    if any(c["severity"] == "major" for c in comparison["conflicted_points"]):
        flags.append("unresolved_major_conflict")
    if coverage_score < 0.5:
        flags.append("low_coverage")

    overall = round(
        (source_diversity * 0.25)
        + (coverage_score * 0.35)
        + ((1 - conflict_ratio) * 0.25)
        + (state["confidence_score"] * 0.15),
        4
    )

    recommendation = "publish" if overall >= 0.70 else "review" if overall >= 0.45 else "reject"

    score = ReportScore(
        overall_confidence=overall,
        source_diversity=source_diversity,
        coverage_score=coverage_score,
        conflict_ratio=conflict_ratio,
        flags=flags,
        recommendation=recommendation
    )

    state["score"] = score.model_dump()
    state["status"] = "scored"
    state["stage"] = "complete"
    return state
```

---

## 12. Confidence-Aware Branching Logic

**File:** `pipeline/edges.py`

The conditional edge after the Comparator reads `state["stage"]` to route between the re-gather branch and the writer.

```python
from pipeline.state import ResearchState

MAX_REGATHER_ATTEMPTS = 2

def comparator_routing_edge(state: ResearchState) -> str:
    """
    Returns the name of the next executor to route to.
    Called by the MAF WorkflowBuilder as a conditional edge.
    """
    if state.get("stage") == "regather":
        if state.get("regather_count", 0) >= MAX_REGATHER_ATTEMPTS:
            # Exceeded retry limit — proceed to writer with available data
            # Flag low confidence in state for scorer to pick up
            state["flags"] = state.get("flags", []) + ["max_regather_exceeded"]
            return "writer"
        return "gatherer"           # Loop back to gatherer with refined queries
    return "writer"
```

### Workflow Graph Definition

**File:** `pipeline/workflow.py`

```python
from agent_framework.workflows import WorkflowBuilder
from pipeline.executors.planner import planner_executor
from pipeline.executors.gatherer import gatherer_executor
from pipeline.executors.extractor import extractor_executor
from pipeline.executors.comparator import comparator_executor
from pipeline.executors.writer import writer_executor
from pipeline.executors.scorer import scorer_executor
from pipeline.edges import comparator_routing_edge

def build_workflow():
    builder = WorkflowBuilder()

    # Register executors
    builder.add_executor("planner",    planner_executor)
    builder.add_executor("gatherer",   gatherer_executor)
    builder.add_executor("extractor",  extractor_executor)
    builder.add_executor("comparator", comparator_executor)
    builder.add_executor("writer",     writer_executor)
    builder.add_executor("scorer",     scorer_executor)

    # Linear edges
    builder.add_edge("planner",    "gatherer")
    builder.add_edge("gatherer",   "extractor")
    builder.add_edge("extractor",  "comparator")

    # Conditional edge: Comparator → Writer or re-Gatherer
    builder.add_conditional_edge(
        source="comparator",
        condition=comparator_routing_edge,
        destinations={"writer": "writer", "gatherer": "gatherer"}
    )

    # Linear edges (post-branch)
    builder.add_edge("writer",  "scorer")

    # Entry and terminal nodes
    builder.set_entry("planner")
    builder.set_terminal("scorer")

    # Enable built-in checkpointing
    builder.enable_checkpointing(
        storage_type="azure_blob",
        connection_string=os.environ["BLOB_CONNECTION_STRING"],
        container="workflow-checkpoints"
    )

    return builder.build()
```

---

## 13. Persistence Layer

### 13.1 Database Schema (DDL)

**File:** `persistence/migrations/V1__initial_schema.sql`

```sql
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
    report_json         NVARCHAR(MAX)   NULL,   -- ResearchReport serialized as JSON
    score_json          NVARCHAR(MAX)   NULL,   -- ReportScore serialized as JSON
    flags               NVARCHAR(MAX)   NULL,   -- JSON array of flag strings
    error_reason        NVARCHAR(MAX)   NULL,
    original_message    NVARCHAR(MAX)   NULL,   -- Original queue payload for replay
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
    source_type         NVARCHAR(20)    NOT NULL,   -- 'web' | 'internal'
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
```

### 13.2 Azure SQL Client

**File:** `persistence/sql_client.py`

Uses `aioodbc` for async SQL Server access. All queries use parameterized statements — no string interpolation.

```python
import aioodbc
import json
import os
from datetime import datetime, timezone
from typing import Optional

# Module-level connection pool — initialized once at worker startup
_pool: aioodbc.Pool | None = None

async def get_pool() -> aioodbc.Pool:
    global _pool
    if _pool is None:
        _pool = await aioodbc.create_pool(
            dsn=os.environ["AZURE_SQL_CONNECTION_STRING"],
            minsize=2,
            maxsize=10,
            autocommit=True
        )
    return _pool

# ------------------------------------------------------------------
# Jobs table
# ------------------------------------------------------------------

async def upsert_job(job: dict) -> None:
    """Insert or update a job record (MERGE on correlation_id)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
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
                    regather_count, flags, original_message, callback_url, submitted_at
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
                job.get("submitted_at", datetime.now(timezone.utc).isoformat())
            )

async def get_job(correlation_id: str) -> Optional[dict]:
    """Fetch a single job record by correlation_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM dbo.Jobs WHERE correlation_id = ?",
                correlation_id
            )
            row = await cur.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cur.description]
            record = dict(zip(columns, row))
            # Deserialize JSON columns
            for col in ("report_json", "score_json", "flags", "original_message"):
                if record.get(col):
                    record[col] = json.loads(record[col])
            return record

async def update_job_status(
    correlation_id: str,
    status: str,
    **extra_fields
) -> None:
    """Lightweight status-only update — avoids a full upsert for mid-pipeline transitions."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            set_clauses = ["status = ?", "updated_at = SYSUTCDATETIME()"]
            params = [status]

            if "stage" in extra_fields:
                set_clauses.append("stage = ?")
                params.append(extra_fields["stage"])
            if "regather_count" in extra_fields:
                set_clauses.append("regather_count = ?")
                params.append(extra_fields["regather_count"])
            if "confidence_score" in extra_fields:
                set_clauses.append("confidence_score = ?")
                params.append(extra_fields["confidence_score"])
            if "error_reason" in extra_fields:
                set_clauses.append("error_reason = ?")
                params.append(extra_fields["error_reason"])
            if "completed_at" in extra_fields:
                set_clauses.append("completed_at = ?")
                params.append(extra_fields["completed_at"])

            params.append(correlation_id)
            await cur.execute(
                f"UPDATE dbo.Jobs SET {', '.join(set_clauses)} WHERE correlation_id = ?",
                *params
            )

# ------------------------------------------------------------------
# Facts table
# ------------------------------------------------------------------

async def insert_facts(correlation_id: str, facts: list[dict]) -> None:
    """Bulk-insert extracted facts for a job. Replaces any existing facts for the run."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Clear previous facts (e.g. after a re-gather loop)
            await cur.execute(
                "DELETE FROM dbo.Facts WHERE correlation_id = ?",
                correlation_id
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
                    f.get("date")
                )
                for f in facts
            ]
            await cur.executemany("""
                INSERT INTO dbo.Facts
                    (correlation_id, claim, source_ref, source_type,
                     topic_tag, confidence_raw, published_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)

async def get_facts(correlation_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM dbo.Facts WHERE correlation_id = ? ORDER BY id",
                correlation_id
            )
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row)) async for row in cur]

# ------------------------------------------------------------------
# ConflictPoints table
# ------------------------------------------------------------------

async def insert_conflicts(correlation_id: str, conflicts: list[dict]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM dbo.ConflictPoints WHERE correlation_id = ?",
                correlation_id
            )
            if not conflicts:
                return
            rows = [
                (
                    correlation_id,
                    c["topic"],
                    c["claim_a"], c["source_a"],
                    c["claim_b"], c["source_b"],
                    c.get("severity", "minor")
                )
                for c in conflicts
            ]
            await cur.executemany("""
                INSERT INTO dbo.ConflictPoints
                    (correlation_id, topic, claim_a, source_a, claim_b, source_b, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)

# ------------------------------------------------------------------
# Citations table
# ------------------------------------------------------------------

async def insert_citations(correlation_id: str, citations: list[dict]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM dbo.Citations WHERE correlation_id = ?",
                correlation_id
            )
            if not citations:
                return
            rows = [
                (
                    correlation_id,
                    c["ref_id"],
                    c["url"],
                    c.get("title", ""),
                    c["source_type"]
                )
                for c in citations
            ]
            await cur.executemany("""
                INSERT INTO dbo.Citations
                    (correlation_id, ref_id, url, title, source_type)
                VALUES (?, ?, ?, ?, ?)
            """, rows)
```

### 13.3 Persist Final Output (Called After Scorer)

The worker calls this helper after the workflow completes to atomically write all four tables in a single transaction:

```python
async def persist_final_output(state: dict) -> None:
    """
    Writes the completed job to all four SQL tables in a single transaction.
    Called once by the worker after workflow.run_async() returns.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        conn.autocommit = False
        try:
            async with conn.cursor() as cur:
                # 1. Update Jobs row with final report + score
                await cur.execute("""
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
                    state["correlation_id"]
                )

            # 2. Insert facts
            await insert_facts(state["correlation_id"], state["extracted_facts"])

            # 3. Insert conflict points
            await insert_conflicts(
                state["correlation_id"],
                state["comparison_result"].get("conflicted_points", [])
            )

            # 4. Insert citations
            await insert_citations(
                state["correlation_id"],
                state["report"].get("citations", [])
            )

            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            conn.autocommit = True
```

### 13.4 Blob Storage — Raw Source Archive

**File:** `persistence/blob_client.py` _(unchanged from previous plan)_

```python
from azure.storage.blob.aio import BlobServiceClient
import json, os

async def archive_sources(correlation_id: str, sources: list) -> None:
    async with BlobServiceClient.from_connection_string(
        os.environ["BLOB_CONNECTION_STRING"]
    ) as blob_service:
        container = blob_service.get_container_client(
            os.environ["BLOB_RAW_SOURCES_CONTAINER"]
        )
        blob_name = f"{correlation_id}/raw_sources.json"
        data = json.dumps(sources, indent=2).encode()
        await container.upload_blob(name=blob_name, data=data, overwrite=True)
```

---

## 14. Cross-Step State Management

### 14.1 ResearchState TypedDict

**File:** `pipeline/state.py`

```python
from typing import TypedDict, Optional, List, Any

class ResearchState(TypedDict):
    # Identity
    correlation_id: str
    prompt: str

    # Status tracking
    status: str             # current pipeline status label
    stage: str              # next executor to run
    regather_count: int     # number of re-gather attempts

    # Pipeline data
    task_plan: dict          # TaskPlan
    raw_sources: List[dict]  # raw fetched content
    extracted_facts: List[dict]    # List[ExtractedFact]
    comparison_result: dict        # ComparisonResult
    confidence_score: float
    report: dict             # ResearchReport
    score: dict              # ReportScore
    flags: List[str]

    # Metadata
    submitted_at: str
    completed_at: Optional[str]
    error_reason: Optional[str]
    original_message: dict   # preserved for replay
```

### 14.2 State Transitions

| Stage Completed                | `status` value | `stage` value (next) |
| ------------------------------ | -------------- | -------------------- |
| Job enqueued                   | `queued`       | `planner`            |
| Planner done                   | `planned`      | `gatherer`           |
| Gatherer done                  | `gathered`     | `extractor`          |
| Extractor done                 | `extracted`    | `comparator`         |
| Comparator done (score ≥ 0.55) | `compared`     | `writer`             |
| Comparator done (score < 0.55) | `compared`     | `regather`           |
| Re-gather complete             | `gathered`     | `extractor`          |
| Writer done                    | `written`      | `scorer`             |
| Scorer done                    | `scored`       | `complete`           |
| Any terminal failure           | `failed`       | —                    |

### 14.3 Checkpoint Writes

The MAF built-in checkpointer writes state to Azure Blob Storage after each executor completes. The checkpoint key is `{correlation_id}/{stage_name}`. On worker restart or resume, the workflow reads the latest checkpoint and resumes from the next unexecuted executor. Mid-pipeline status transitions (e.g. `planned`, `gathered`) are written to `dbo.Jobs` via `update_job_status()` at the start of each executor so the poll endpoint always reflects current progress.

---

## 15. Error Handling and Dead-Letter Strategy

### 15.1 Queue Worker with Retry

**File:** `worker/queue_worker.py`

```python
import asyncio
import base64
import json
import logging
from azure.storage.queue.aio import QueueClient
from azure.identity.aio import DefaultAzureCredential
from pipeline.workflow import build_workflow
from persistence.sql_client import update_job_status
import os

MAX_DEQUEUE_COUNT = 5      # Azure Queue Storage default poison threshold
VISIBILITY_TIMEOUT = 300   # 5 minutes per processing attempt

logger = logging.getLogger(__name__)

async def process_message(message: dict, receipt_handle: str, queue_client: QueueClient):
    correlation_id = message["correlation_id"]
    try:
        workflow = build_workflow()
        initial_state = {
            "correlation_id": correlation_id,
            "prompt": message["prompt"],
            "source_hints": message.get("source_hints", []),
            "depth": message.get("depth", "standard"),
            "status": "processing",
            "stage": "planner",
            "regather_count": 0,
            "flags": [],
            "original_message": message
        }

        await update_job_status(correlation_id, "processing")
        final_state = await workflow.run_async(
            thread_id=correlation_id,
            initial_state=initial_state
        )

        # Atomically persist all four SQL tables in a single transaction
        from persistence.sql_client import persist_final_output
        await persist_final_output(final_state)

        # Delete message on success
        await queue_client.delete_message(message_id=receipt_handle)
        logger.info(f"Job {correlation_id} completed successfully")

    except Exception as exc:
        logger.error(f"Job {correlation_id} failed: {exc}")
        await update_job_status(
            correlation_id,
            "failed",
            error_reason=str(exc)
        )
        # Do NOT delete message — Azure will re-enqueue up to MAX_DEQUEUE_COUNT
        # After max attempts, Azure auto-moves to *-poison queue (dead-letter)
        raise

async def run_worker():
    credential = DefaultAzureCredential()
    queue_client = QueueClient(
        account_url=f"https://{os.environ['AZURE_STORAGE_ACCOUNT_NAME']}.queue.core.windows.net",
        queue_name=os.environ["AZURE_STORAGE_QUEUE_NAME"],
        credential=credential
    )

    logger.info("Worker started — polling queue")
    async with queue_client:
        while True:
            messages = await queue_client.receive_messages(
                max_messages=1,
                visibility_timeout=VISIBILITY_TIMEOUT
            )
            processed = False
            async for msg in messages:
                processed = True
                payload = json.loads(base64.b64decode(msg.content).decode())
                await process_message(payload, msg.id, queue_client)
                await queue_client.delete_message(msg.id, msg.pop_receipt)

            if not processed:
                await asyncio.sleep(5)   # Back off when queue is empty

if __name__ == "__main__":
    asyncio.run(run_worker())
```

### 15.2 Dead-Letter Queue Behaviour

Azure Queue Storage automatically moves messages to the `<queue-name>-poison` queue when the dequeue count reaches the configured maximum (default 5). The poison queue acts as the dead-letter store.

- Messages in the poison queue are **not automatically deleted** — they persist for manual inspection and replay
- The `POST /replay/{correlation_id}` endpoint reads the Cosmos DB record for the original message, re-encodes it, and publishes back to the main queue
- Replayed jobs inherit the original `correlation_id` so all existing state and checkpoints are preserved

### 15.3 Per-Executor Retry Configuration

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, TimeoutError))
)
async def tavily_search_with_retry(query: str):
    return await tavily_search(query)
```

All external calls (Tavily, Azure AI Search, Azure AI Foundry inference) are wrapped with `tenacity` retry decorators with exponential backoff and a 3-attempt ceiling. Failures after 3 attempts surface as exceptions caught by the worker's outer try/except.

---

## 16. Final Output

### 16.1 JSON Response Structure

Returned by `GET /research/{correlation_id}` once status is `complete`:

```json
{
  "correlation_id": "abc-123",
  "status": "complete",
  "submitted_at": "2025-01-01T10:00:00Z",
  "completed_at": "2025-01-01T10:04:32Z",
  "score": {
    "overall_confidence": 0.83,
    "source_diversity": 0.9,
    "coverage_score": 0.8,
    "conflict_ratio": 0.1,
    "flags": [],
    "recommendation": "publish"
  },
  "report": {
    "title": "...",
    "executive_summary": "...",
    "findings": [
      {
        "sub_query": "...",
        "summary": "...",
        "supporting_citations": ["ref-1", "ref-2"],
        "confidence": 0.87
      }
    ],
    "agreed_points": ["..."],
    "conflicted_points": [
      {
        "topic": "...",
        "claim_a": "...",
        "source_a": "https://...",
        "claim_b": "...",
        "source_b": "https://...",
        "severity": "minor"
      }
    ],
    "open_questions": ["..."],
    "citations": [
      {
        "ref_id": "ref-1",
        "url": "https://...",
        "title": "...",
        "source_type": "web",
        "accessed_at": "2025-01-01T10:01:15Z"
      }
    ],
    "methodology_note": "..."
  }
}
```

### 16.2 Callback Notification (Optional)

If `callback_url` was provided in the original request, the worker POSTs the final output JSON to that URL on completion:

```python
if state.get("original_message", {}).get("callback_url"):
    async with httpx.AsyncClient() as http:
        await http.post(
            state["original_message"]["callback_url"],
            json=final_output,
            timeout=10
        )
```

---

## 17. Monitoring and Tracing

### 17.1 OpenTelemetry Setup

**File:** `monitoring/telemetry.py`

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
import os

def setup_telemetry():
    exporter = AzureMonitorTraceExporter(
        connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"]
    )
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer("research-agent")
```

### 17.2 Instrumented Executor Pattern

Each executor wraps its logic in a named span carrying the `correlation_id` as an attribute:

```python
from monitoring.telemetry import tracer

async def planner_executor(context, state: ResearchState) -> ResearchState:
    with tracer.start_as_current_span("planner_executor") as span:
        span.set_attribute("correlation_id", state["correlation_id"])
        span.set_attribute("prompt_length", len(state["prompt"]))
        # ... executor logic ...
        span.set_attribute("sub_query_count", len(plan.sub_queries))
    return state
```

### 17.3 Key Metrics to Track in Application Insights

| Metric                      | Description                               |
| --------------------------- | ----------------------------------------- |
| `pipeline.job.duration`     | End-to-end job duration by correlation ID |
| `pipeline.stage.duration`   | Per-executor latency (P50, P95)           |
| `pipeline.confidence_score` | Distribution of final confidence scores   |
| `pipeline.regather.count`   | How often re-gather branch is triggered   |
| `pipeline.error.rate`       | Failed jobs per hour                      |
| `queue.depth`               | Azure Queue Storage message backlog       |
| `deadletter.count`          | Poison queue depth (alert at > 0)         |

---

## 18. Guardrails and Safety

### 18.1 Prompt Injection Filter

**File:** `tools/injection_filter.py`

```python
import re
from typing import Optional

INJECTION_PATTERNS = [
    r"ignore (all |previous |above )?instructions",
    r"you are now",
    r"system prompt",
    r"act as (a |an )?",
    r"jailbreak",
    r"DAN mode",
    r"<\|.*?\|>",             # Common injection delimiters
    r"\[INST\]",
    r"###\s*(System|Human|Assistant)",
]

def sanitize_prompt(prompt: str) -> Optional[str]:
    """Returns sanitized prompt, or None if prompt fails safety check."""
    lower = prompt.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lower):
            return None      # Caller should return HTTP 400
    # Strip control characters
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", prompt)
    return cleaned.strip()
```

### 18.2 Schema Enforcement

Every LLM response is validated through Pydantic's `model_validate_json`. If validation fails on the first attempt, the executor retries once with an explicit correction prompt:

```python
try:
    result = SomeSchema.model_validate_json(raw_response)
except ValidationError as e:
    # Single correction retry
    correction_response = await client.inference.get_chat_completions(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": raw_response},
            {"role": "user", "content": f"Your response failed schema validation: {e}. Return corrected JSON only."}
        ],
        response_format={"type": "json_object"}
    )
    result = SomeSchema.model_validate_json(correction_response.choices[0].message.content)
```

### 18.3 Content Scope Guard

A pre-Planner refusal check determines whether the research topic is within acceptable scope:

```python
SCOPE_CHECK_PROMPT = """
Evaluate whether the following research prompt is within acceptable scope for a business
research tool. Respond with JSON: {"allowed": true/false, "reason": "..."}.
Reject prompts requesting: personal data lookup, harmful content generation,
legal/medical advice, or content violating usage policy.
"""

async def check_scope(prompt: str) -> bool:
    # ... call Azure AI Foundry with SCOPE_CHECK_PROMPT ...
    result = json.loads(response)
    return result["allowed"]
```

---

## 19. Evaluation Harness

**File:** `evaluation/harness.py`

### 19.1 Deterministic Fixtures

```python
# evaluation/fixtures/fixtures.py

FIXTURES = [
    {
        "id": "fx-001",
        "prompt": "Compare the market positions of AWS, Azure, and GCP in enterprise cloud adoption as of 2024.",
        "expected": {
            "min_fact_count": 10,
            "min_source_count": 2,
            "must_have_conflicts": False,
            "min_confidence_score": 0.65,
            "required_sub_query_topics": ["AWS", "Azure", "GCP"],
            "must_have_citations": True
        }
    },
    {
        "id": "fx-002",
        "prompt": "What are the regulatory requirements for AI systems in the EU and the US?",
        "expected": {
            "min_fact_count": 8,
            "min_source_count": 2,
            "must_have_conflicts": True,     # EU vs US diverge
            "min_confidence_score": 0.55,
            "required_sub_query_topics": ["EU AI Act", "US regulation"],
            "must_have_citations": True
        }
    }
]
```

### 19.2 Scoring Harness

```python
import pytest
import asyncio
from pipeline.workflow import build_workflow
from evaluation.fixtures.fixtures import FIXTURES

def score_output(result: dict, expected: dict) -> dict:
    checks = {}

    report = result.get("report", {})
    facts = result.get("_facts", [])

    checks["schema_valid"] = all(k in report for k in [
        "title", "executive_summary", "findings",
        "agreed_points", "open_questions", "citations"
    ])
    checks["min_fact_count"] = len(facts) >= expected["min_fact_count"]
    checks["min_source_count"] = len(set(f["source_ref"] for f in facts)) >= expected["min_source_count"]
    checks["confidence_score_ok"] = result.get("confidence_score", 0) >= expected["min_confidence_score"]
    checks["has_citations"] = len(report.get("citations", [])) > 0 if expected["must_have_citations"] else True
    if expected["must_have_conflicts"]:
        checks["has_conflicts"] = len(report.get("conflicted_points", [])) > 0

    passed = sum(checks.values())
    total = len(checks)
    return {"checks": checks, "pass_rate": passed / total, "passed": passed == total}

@pytest.mark.parametrize("fixture", FIXTURES)
def test_fixture(fixture):
    workflow = build_workflow()
    initial_state = {
        "correlation_id": f"test-{fixture['id']}",
        "prompt": fixture["prompt"],
        "status": "processing",
        "stage": "planner",
        "regather_count": 0,
        "flags": [],
        "original_message": {}
    }
    final_state = asyncio.run(workflow.run_async(
        thread_id=initial_state["correlation_id"],
        initial_state=initial_state
    ))
    scoring = score_output(final_state, fixture["expected"])
    assert scoring["passed"], f"Fixture {fixture['id']} failed checks: {scoring['checks']}"
```

---

## 20. Deployment

### 20.1 Python Dependencies

```
# requirements.txt
agent-framework>=0.1.0
fastapi>=0.111.0
uvicorn>=0.29.0
azure-storage-queue>=12.9.0
azure-storage-blob>=12.19.0
aioodbc>=0.5.0
pyodbc>=5.1.0
azure-ai-projects>=1.0.0b1
azure-search-documents>=11.6.0
azure-identity>=1.15.0
azure-monitor-opentelemetry-exporter>=1.0.0b21
opentelemetry-sdk>=1.24.0
pydantic>=2.7.0
httpx>=0.27.0
tenacity>=8.3.0
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

### 20.2 Dockerfile

```dockerfile
FROM python:3.11-slim

# Install Microsoft ODBC Driver 18 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg unixodbc-dev \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/12/prod.list \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# API service
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 20.3 Worker Service (Separate Container)

```dockerfile
FROM python:3.11-slim

# Install Microsoft ODBC Driver 18 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg unixodbc-dev \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/12/prod.list \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "worker.queue_worker"]
```

### 20.4 Azure Container Apps — Two Services

```yaml
# API service: scales 1–5 replicas based on HTTP concurrency
# Worker service: scales 0–10 replicas based on Azure Queue depth (KEDA scaler)

KEDA trigger for worker:
  type: azure-queue
  metadata:
    queueName: research-jobs
    queueLength: "5" # scale up when queue depth > 5 per replica
```

---

## 21. End-to-End Flow Summary

```
1.  Client POSTs { prompt, source_hints, depth } to POST /research
2.  FastAPI validates payload → assigns correlation_id → enqueues to Azure Queue Storage
3.  FastAPI returns 202 { correlation_id, poll_url }

4.  Worker dequeues message → initialises ResearchState → writes status "processing" to Azure SQL (dbo.Jobs)
5.  MAF Workflow starts at Planner executor
        → SQL UPDATE dbo.Jobs: status = "planned"
6.  Source Gatherer runs Tavily + Azure AI Search in parallel
        → raw sources archived to Blob Storage
        → SQL UPDATE dbo.Jobs: status = "gathered"
7.  Extractor iterates sources → structured facts extracted via Azure AI Foundry
        → SQL UPDATE dbo.Jobs: status = "extracted"
8.  Comparator groups facts → scores confidence
        → if score < 0.55 AND regather_count < 2: branch back to Gatherer
        → else: proceed to Writer
        → SQL UPDATE dbo.Jobs: status = "compared"
9.  Writer generates structured report JSON
        → SQL UPDATE dbo.Jobs: status = "written"
10. Scorer computes composite quality score + flags
        → SQL UPDATE dbo.Jobs: status = "scored"
11. persist_final_output() runs in a single SQL transaction:
        → UPDATE dbo.Jobs: status = "complete", report_json, score_json, confidence_score
        → BULK INSERT dbo.Facts (all extracted facts)
        → BULK INSERT dbo.ConflictPoints (all identified conflicts)
        → BULK INSERT dbo.Citations (all report citations)

12. Client polls GET /research/{correlation_id} → SELECT from dbo.Jobs → receives final JSON response
13. (Optional) Callback URL notified via HTTP POST with final output

--- FAILURE PATH ---
A.  Any executor fails after 3 tenacity retries → exception bubbles to worker
B.  Worker catches → SQL UPDATE dbo.Jobs: status = "failed", error_reason = <message>
C.  Message NOT deleted → Azure Queue increments dequeue_count
D.  After 5 dequeue attempts → Azure moves message to research-jobs-poison queue
E.  Alert fires on poison queue depth > 0 (App Insights)
F.  Operator calls POST /replay/{correlation_id} → reads original_message from dbo.Jobs → re-enqueues
G.  MAF checkpointer resumes workflow from last successful stage checkpoint
```

---

_Last updated: 2026-03-20_  
_Framework: Microsoft Agent Framework (public preview)_
