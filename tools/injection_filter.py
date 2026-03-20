"""
Prompt injection pre-processor.

Scans user prompts for common injection patterns and strips control
characters.  Returns ``None`` if the prompt fails the safety check,
signalling the caller to reject it with HTTP 400.
"""

from __future__ import annotations

import re
from typing import Optional

from core.logging import get_logger

logger = get_logger(__name__, component="injection_filter")

# Regex patterns that indicate prompt injection attempts
INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+|previous\s+|above\s+)*instructions",
        r"you are now",
        r"system prompt",
        r"act as (a |an )?",
        r"jailbreak",
        r"DAN mode",
        r"<\|.*?\|>",            # Common injection delimiters
        r"\[INST\]",
        r"###\s*(System|Human|Assistant)",
    ]
]


def sanitize_prompt(prompt: str) -> Optional[str]:
    """
    Validate and sanitize a user-provided research prompt.

    Args:
        prompt: Raw user input.

    Returns:
        The cleaned prompt string, or ``None`` if the prompt matches
        a known injection pattern (caller should return HTTP 400).
    """
    for pattern in INJECTION_PATTERNS:
        if pattern.search(prompt):
            logger.warning(
                "Prompt rejected — matched injection pattern",
                extra={"pattern": pattern.pattern},
            )
            return None

    # Strip control characters (keep newlines and tabs)
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", prompt)
    return cleaned.strip()
