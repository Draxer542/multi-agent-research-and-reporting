"""
MAF Workflow graph definition for the research pipeline.

Constructs the directed graph of executors with conditional branching
using the Microsoft Agent Framework ``WorkflowBuilder``.

Graph::

    planner → gatherer → extractor → comparator
                  ↑                       │
                  │  (re-gather)   ┌──────┴──────┐
                  └────────────────┤ score < 0.55│
                                   └──────┬──────┘
                                          │ (proceed)
                                          ↓
                                       writer → scorer → [output]
"""

from __future__ import annotations

from agent_framework import WorkflowBuilder

from pipeline.executors.planner import planner_executor
from pipeline.executors.gatherer import gatherer_executor
from pipeline.executors.extractor import extractor_executor
from pipeline.executors.comparator import comparator_executor
from pipeline.executors.writer import writer_executor
from pipeline.executors.scorer import scorer_executor
from pipeline.edges import should_regather, should_write


def build_workflow():
    """Build and return the research pipeline workflow."""
    workflow = (
        WorkflowBuilder(start_executor=planner_executor)
        # Linear: Planner → Gatherer → Extractor → Comparator
        .add_edge(planner_executor, gatherer_executor)
        .add_edge(gatherer_executor, extractor_executor)
        .add_edge(extractor_executor, comparator_executor)
        # Conditional: Comparator → Writer (proceed) or Gatherer (re-gather)
        .add_edge(comparator_executor, writer_executor, condition=should_write)
        .add_edge(comparator_executor, gatherer_executor, condition=should_regather)
        # Linear: Writer → Scorer (terminal)
        .add_edge(writer_executor, scorer_executor)
        .build()
    )
    return workflow
