"""
Web content cleaner — strips noise from scraped HTML-to-markdown.

Removes newsletter sign-ups, cookie banners, social share widgets,
image placeholder alt-text, sidebar ads, and other non-content noise
commonly found in Tavily web scraping results.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Noise patterns (compiled once at import) ──────────────────────────────

_NOISE_PATTERNS: list[re.Pattern] = [
    # Newsletter / subscription blocks
    re.compile(
        r"(?:subscribe|sign[\s-]?up|newsletter|get curated|your subscription"
        r"|unsubscribe|email address|privacy (?:statement|policy))"
        r".*?(?:\n{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
    # Cookie/consent notices
    re.compile(
        r"(?:cookie|consent|we use cookies|accept all).*?(?:\n{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
    # Share / social media widgets
    re.compile(
        r"(?:^|\n)#+\s*share\b.*?(?:\n{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:share on|tweet this|link copied|follow us).*?\n",
        re.IGNORECASE,
    ),
    # Related content / promo blocks
    re.compile(
        r"(?:related (?:solutions|articles|posts|content)|take the next step"
        r"|explore [\w\s]+|read the (?:ebook|guide|article)"
        r"|watch all episodes|join our).*?(?:\n{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
    # Image markdown placeholders with empty or generic alt-text
    re.compile(r"!\[(?:[^\]]*)\]\([^\)]+\)\s*\n?"),
    # Repeated blank lines
    re.compile(r"\n{3,}"),
    # "Industry newsletter" / "Take the next step" type CTA headers
    re.compile(
        r"(?:^|\n)#+\s*(?:industry newsletter|take the next step|"
        r"resources|related solutions|thank you).*?(?:\n{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
    # Footnote-style references at the end (numbered lists of URLs)
    re.compile(
        r"(?:^|\n)\d+\.\s*\[.*?\]\(https?://.*?\).*?$",
        re.MULTILINE,
    ),
]


def clean_web_content(
    content: str,
    max_length: int = 6000,
) -> str:
    """
    Remove noise from web-scraped markdown content.

    Args:
        content: Raw markdown content from Tavily or similar scraper.
        max_length: Maximum character length of the cleaned output.

    Returns:
        Cleaned content string, truncated to ``max_length``.
    """
    if not content:
        return ""

    text = content

    # Apply noise removal patterns
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub("\n", text)

    # Collapse remaining whitespace runs
    text = re.sub(r"[ \t]+\n", "\n", text)     # trailing whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)     # triple+ newlines
    text = text.strip()

    # Truncate to max_length at a sentence boundary
    if len(text) > max_length:
        cut = text[:max_length]
        # Try to break at the last sentence-ending punctuation
        last_period = max(
            cut.rfind(". "),
            cut.rfind(".\n"),
            cut.rfind("? "),
            cut.rfind("! "),
        )
        if last_period > max_length * 0.6:
            text = cut[: last_period + 1]
        else:
            text = cut

    return text


def clean_internal_content(
    content: str,
    max_length: int = 6000,
) -> str:
    """
    Light cleanup for internal document chunks (from Azure AI Search).

    Internal docs are already relatively clean — just normalise whitespace
    and truncate.
    """
    if not content:
        return ""

    text = re.sub(r"\n{3,}", "\n\n", content)
    text = text.strip()

    if len(text) > max_length:
        text = text[:max_length]

    return text
