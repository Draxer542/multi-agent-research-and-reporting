"""
Evaluation scoring harness.

Runs pipeline fixtures end-to-end and validates the output against
expected quality thresholds.  Can be invoked with pytest::

    pytest evaluation/harness.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from pipeline.workflow import build_workflow
from evaluation.fixtures.fixtures import FIXTURES


def score_output(result: dict, expected: dict) -> dict:
    """
    Compare pipeline output against a fixture's expectations.

    Returns a dict with individual check results and an overall pass rate.
    """
    checks: dict[str, bool] = {}

    report = result.get("report", {})
    facts = result.get("extracted_facts", [])

    checks["schema_valid"] = all(
        k in report
        for k in [
            "title",
            "executive_summary",
            "findings",
            "agreed_points",
            "open_questions",
            "citations",
        ]
    )
    checks["min_fact_count"] = len(facts) >= expected["min_fact_count"]
    checks["min_source_count"] = (
        len(set(f["source_ref"] for f in facts))
        >= expected["min_source_count"]
    )
    checks["confidence_score_ok"] = (
        result.get("confidence_score", 0)
        >= expected["min_confidence_score"]
    )
    checks["has_citations"] = (
        len(report.get("citations", [])) > 0
        if expected["must_have_citations"]
        else True
    )
    if expected["must_have_conflicts"]:
        checks["has_conflicts"] = (
            len(report.get("conflicted_points", [])) > 0
        )

    passed = sum(checks.values())
    total = len(checks)
    return {
        "checks": checks,
        "pass_rate": passed / total if total else 0,
        "passed": passed == total,
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_fixture(fixture: dict) -> None:
    """Run a single evaluation fixture through the full pipeline."""
    workflow = build_workflow()
    initial_state = {
        "correlation_id": f"test-{fixture['id']}",
        "prompt": fixture["prompt"],
        "status": "processing",
        "stage": "planner",
        "regather_count": 0,
        "flags": [],
        "original_message": {},
    }
    final_state = asyncio.run(
        workflow.run_async(
            thread_id=initial_state["correlation_id"],
            initial_state=initial_state,
        )
    )
    scoring = score_output(final_state, fixture["expected"])
    assert scoring["passed"], (
        f"Fixture {fixture['id']} failed checks: {scoring['checks']}"
    )
