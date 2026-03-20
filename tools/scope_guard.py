"""
Content Scope Guard — pre-Planner LLM check.

Evaluates whether a research prompt falls within acceptable scope
for a business research tool.  Rejects prompts requesting personal
data lookup, harmful content, legal/medical advice, or policy
violations.

Usage::

    from tools.scope_guard import check_scope
    allowed = await check_scope("Compare cloud providers")
    if not allowed:
        raise HTTPException(400, "Prompt out of scope")
"""

from __future__ import annotations

import json

from core.config import get_settings
from core.openai_client import get_openai_client
from core.logging import get_logger

logger = get_logger(__name__, component="scope_guard")

SCOPE_CHECK_PROMPT = """\
You are a permissive scope guard for a business research tool. Your job is to
ALLOW most prompts and only reject clearly inappropriate ones.

Respond with JSON: {"allowed": true, "reason": "..."} or {"allowed": false, "reason": "..."}.

ONLY REJECT prompts that are clearly:
- Requesting personal data lookup or surveillance of individuals
- Requesting harmful, violent, or illegal content generation
- Requesting specific legal or medical advice for a real case
- Completely unrelated to research (e.g., "write me a poem", "help me code")

ALWAYS ALLOW prompts that involve:
- Market research, competitive analysis, industry comparisons
- Technology comparisons and evaluations
- Regulatory or compliance overviews
- Company or vendor analysis and due diligence
- Trend analysis, forecasting, or feasibility studies
- Any type of business or academic research topic

When in doubt, ALLOW the prompt. Err on the side of permissiveness.
"""


async def check_scope(prompt: str) -> bool:
    """
    Return ``True`` if *prompt* is within acceptable research scope.

    Makes a lightweight LLM call to evaluate the prompt.  Falls back to
    ``True`` (permissive) if the LLM call fails, to avoid blocking the
    pipeline on transient errors.
    """
    settings = get_settings()

    if not settings.azure_openai_api_key:
        logger.warning("API key not configured — skipping scope check")
        return True

    try:
        client = get_openai_client()
        response = await client.chat.completions.create(
            model=settings.azure_openai_deployment_name,
            messages=[
                {"role": "system", "content": SCOPE_CHECK_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=150,
        )

        raw = response.choices[0].message.content
        result = json.loads(raw)
        allowed = result.get("allowed", True)

        logger.info(
            "Scope guard result",
            extra={
                "allowed": allowed,
                "reason": result.get("reason", "unspecified"),
            },
        )

        if not allowed:
            logger.warning(
                "Prompt rejected by scope guard",
                extra={"reason": result.get("reason", "unspecified")},
            )
        return allowed

    except Exception as exc:
        # Fail-open: if scope check itself errors, allow through
        logger.error(
            "Scope guard LLM call failed — defaulting to allow",
            extra={"error": str(exc)},
        )
        return True
