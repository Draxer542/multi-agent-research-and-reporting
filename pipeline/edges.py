"""
Conditional edge definitions for the research pipeline.

Provides condition functions used by ``WorkflowBuilder.add_edge()``
to route messages from the Comparator to either the Writer (proceed)
or the Gatherer (re-gather when confidence is low).
"""

from __future__ import annotations

from typing import Any

from pipeline.state import ResearchState

MAX_REGATHER_ATTEMPTS = 2


def should_regather(message: Any) -> bool:
    """
    Condition: return ``True`` when the comparator wants to re-gather
    AND the re-gather limit has not been exceeded.
    """
    if not isinstance(message, ResearchState):
        return False

    if message.stage == "regather" and message.regather_count < MAX_REGATHER_ATTEMPTS:
        return True
    return False


def should_write(message: Any) -> bool:
    """
    Condition: return ``True`` when the comparator says proceed to writer,
    OR when re-gather limit has been exceeded (fall through to writer).
    """
    if not isinstance(message, ResearchState):
        return True  # default pass-through

    if message.stage == "writer":
        return True

    # Exceeded re-gather limit — proceed anyway
    if message.stage == "regather" and message.regather_count >= MAX_REGATHER_ATTEMPTS:
        message.flags.append("max_regather_exceeded")
        message.stage = "writer"
        return True

    return False
