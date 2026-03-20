"""
Deterministic test fixtures for the evaluation harness.

Each fixture defines a research prompt and the minimum quality
thresholds that the pipeline's output must satisfy.
"""

from __future__ import annotations

FIXTURES = [
    {
        "id": "fx-001",
        "prompt": (
            "Compare the market positions of AWS, Azure, and GCP "
            "in enterprise cloud adoption as of 2024."
        ),
        "expected": {
            "min_fact_count": 10,
            "min_source_count": 2,
            "must_have_conflicts": False,
            "min_confidence_score": 0.65,
            "required_sub_query_topics": ["AWS", "Azure", "GCP"],
            "must_have_citations": True,
        },
    },
    {
        "id": "fx-002",
        "prompt": (
            "What are the regulatory requirements for AI systems "
            "in the EU and the US?"
        ),
        "expected": {
            "min_fact_count": 8,
            "min_source_count": 2,
            "must_have_conflicts": True,     # EU vs US diverge
            "min_confidence_score": 0.55,
            "required_sub_query_topics": ["EU AI Act", "US regulation"],
            "must_have_citations": True,
        },
    },
]
