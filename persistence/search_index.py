"""
Azure AI Search index management for the internal-docs corpus.

Creates, updates, and manages the ``internal-docs`` search index with
full-text, vector (HNSW cosine 1536d), and semantic search capabilities.

Standalone usage::

    python -m persistence.search_index          # create index
    python -m persistence.search_index --ingest  # create index + ingest PDFs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from azure.search.documents import SearchClient as SyncSearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    HnswParameters,
    VectorSearchProfile,
    SemanticConfiguration,
    SemanticSearch,
    SemanticPrioritizedFields,
    SemanticField,
)
from azure.core.credentials import AzureKeyCredential

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__, component="search_index")


# ── Client helpers ─────────────────────────────────────────────────────────

def get_index_client() -> SearchIndexClient:
    """Return a SearchIndexClient using API key from settings."""
    settings = get_settings()
    return SearchIndexClient(
        endpoint=settings.azure_search_endpoint,
        credential=AzureKeyCredential(settings.azure_search_key),
    )


def get_search_client(index_name: str = "internal-docs") -> SyncSearchClient:
    """Return a sync SearchClient for document upload."""
    settings = get_settings()
    return SyncSearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(settings.azure_search_key),
    )


# ── Index schema ───────────────────────────────────────────────────────────

def build_index_definition() -> SearchIndex:
    """Build the full index schema with vector + semantic search."""
    fields = [
        # ── Identity
        SimpleField(name="id", type=SearchFieldDataType.String,
                    key=True, retrievable=True, filterable=False),
        SimpleField(name="source_url", type=SearchFieldDataType.String,
                    retrievable=True, filterable=False),
        SimpleField(name="file_name", type=SearchFieldDataType.String,
                    retrievable=True, filterable=True),

        # ── Bibliographic metadata
        SearchableField(name="title", type=SearchFieldDataType.String,
                        retrievable=True, analyzer_name="en.microsoft"),
        SearchField(name="authors",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    retrievable=True, searchable=True, filterable=True),
        SearchableField(name="venue", type=SearchFieldDataType.String,
                        retrievable=True, filterable=True),
        SimpleField(name="publication_year", type=SearchFieldDataType.Int32,
                    retrievable=True, filterable=True, sortable=True),

        # ── Content
        SearchableField(name="abstract", type=SearchFieldDataType.String,
                        retrievable=True, analyzer_name="en.microsoft"),
        SearchableField(name="content", type=SearchFieldDataType.String,
                        retrievable=True, analyzer_name="en.microsoft"),
        SearchableField(name="section_title", type=SearchFieldDataType.String,
                        retrievable=True, analyzer_name="en.microsoft"),
        SimpleField(name="section_type", type=SearchFieldDataType.String,
                    retrievable=True, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32,
                    retrievable=True, filterable=False, sortable=True),

        # ── Research classification
        SearchField(name="research_domain",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    retrievable=True, searchable=True, filterable=True),
        SearchField(name="key_concepts",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    retrievable=True, searchable=True, filterable=True),
        SimpleField(name="paper_type", type=SearchFieldDataType.String,
                    retrievable=True, filterable=True),

        # ── Provenance
        SimpleField(name="indexed_at", type=SearchFieldDataType.DateTimeOffset,
                    retrievable=True, filterable=True, sortable=True),
        SimpleField(name="last_updated", type=SearchFieldDataType.DateTimeOffset,
                    retrievable=True, filterable=True, sortable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hnsw-config",
                parameters=HnswParameters(
                    metric="cosine", m=4,
                    ef_construction=400, ef_search=500,
                ),
            )
        ],
        profiles=[
            VectorSearchProfile(
                name="vector-profile",
                algorithm_configuration_name="hnsw-config",
            )
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[
                        SemanticField(field_name="content"),
                        SemanticField(field_name="abstract"),
                    ],
                    keywords_fields=[
                        SemanticField(field_name="key_concepts"),
                        SemanticField(field_name="section_title"),
                    ],
                ),
            )
        ]
    )

    return SearchIndex(
        name="internal-docs",
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


# ── Index CRUD ─────────────────────────────────────────────────────────────

def create_index(overwrite: bool = False) -> None:
    """Create the internal-docs search index."""
    client = get_index_client()
    index_name = "internal-docs"

    if overwrite:
        try:
            client.delete_index(index_name)
            logger.info("Deleted existing index", extra={"index": index_name})
        except Exception:
            pass

    index = build_index_definition()
    result = client.create_or_update_index(index)
    logger.info(
        "Index created/updated",
        extra={
            "index_name": result.name,
            "field_count": len(result.fields),
        },
    )
    print(f"✅ Index created: {result.name} ({len(result.fields)} fields)")


def index_exists() -> bool:
    """Check whether the internal-docs index exists."""
    client = get_index_client()
    try:
        client.get_index("internal-docs")
        return True
    except Exception:
        return False


# ── PDF chunking + ingestion ───────────────────────────────────────────────

CHUNK_SIZE = 2000  # characters per chunk (with overlap)
CHUNK_OVERLAP = 200


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if c.strip()]


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract all text from a PDF using PyMuPDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)


def extract_metadata_from_text(text: str, filename: str) -> dict:
    """Extract basic metadata from the first page of text."""
    lines = [l.strip() for l in text[:3000].split("\n") if l.strip()]

    # Title is typically the first substantial line
    title = filename.replace(".pdf", "")
    for line in lines[:5]:
        if len(line) > 10 and not line.startswith("arXiv"):
            title = line
            break

    # Try to find abstract
    abstract = ""
    text_lower = text[:5000].lower()
    abs_start = text_lower.find("abstract")
    if abs_start != -1:
        abs_text = text[abs_start + len("abstract"):abs_start + 2000]
        # Take until "introduction" or "1." section
        for end_marker in ["introduction", "\n1 ", "\n1.", "keywords"]:
            end_pos = abs_text.lower().find(end_marker)
            if end_pos != -1:
                abs_text = abs_text[:end_pos]
                break
        abstract = abs_text.strip()[:1500]

    return {
        "title": title[:200],
        "abstract": abstract,
    }


def ingest_pdfs(papers_dir: str = "research_papers") -> None:
    """
    Extract text from PDFs, chunk them, and upload to Azure AI Search.
    """
    papers_path = Path(papers_dir)
    if not papers_path.exists():
        logger.error("Papers directory not found", extra={"path": papers_dir})
        print(f"❌ Directory not found: {papers_dir}")
        return

    pdfs = list(papers_path.glob("*.pdf"))
    if not pdfs:
        logger.warning("No PDF files found", extra={"path": papers_dir})
        print(f"❌ No PDFs found in {papers_dir}")
        return

    print(f"Found {len(pdfs)} PDFs to ingest")
    client = get_search_client()
    now = datetime.now(timezone.utc).isoformat()
    total_docs = 0

    for pdf_path in pdfs:
        print(f"\n📄 Processing: {pdf_path.name}")

        try:
            full_text = extract_pdf_text(pdf_path)
        except Exception as exc:
            print(f"   ❌ Failed to extract text: {exc}")
            continue

        meta = extract_metadata_from_text(full_text, pdf_path.name)
        chunks = chunk_text(full_text)
        print(f"   Title: {meta['title'][:60]}...")
        print(f"   Abstract: {len(meta['abstract'])} chars")
        print(f"   Chunks: {len(chunks)}")

        documents = []
        for idx, chunk in enumerate(chunks):
            doc_id = hashlib.md5(
                f"{pdf_path.name}:{idx}".encode()
            ).hexdigest()

            documents.append({
                "id": doc_id,
                "source_url": f"file://{pdf_path.name}",
                "file_name": pdf_path.name,
                "title": meta["title"],
                "authors": [],  # Could be extracted with better parsing
                "venue": "",
                "publication_year": 0,
                "abstract": meta["abstract"] if idx == 0 else "",
                "content": chunk,
                "section_title": "",
                "section_type": "body",
                "chunk_index": idx,
                "research_domain": [],
                "key_concepts": [],
                "paper_type": "research",
                "indexed_at": now,
                "last_updated": now,
            })

        # Upload in batches of 100
        for batch_start in range(0, len(documents), 100):
            batch = documents[batch_start:batch_start + 100]
            result = client.upload_documents(documents=batch)
            succeeded = sum(1 for r in result if r.succeeded)
            failed = len(batch) - succeeded
            print(f"   Uploaded batch: {succeeded} ok, {failed} failed")
            total_docs += succeeded

    print(f"\n✅ Ingestion complete: {total_docs} chunks indexed")


# ── CLI entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage internal-docs search index")
    parser.add_argument("--ingest", action="store_true",
                        help="Ingest PDFs from research_papers/ after creating index")
    parser.add_argument("--overwrite", action="store_true",
                        help="Delete and recreate the index")
    args = parser.parse_args()

    create_index(overwrite=args.overwrite)

    if args.ingest:
        ingest_pdfs()