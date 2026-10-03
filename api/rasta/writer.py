"""
Agent 4 - the Brief Writer.  LLM.

Turns computed facts into something an NGO programme officer could paste into a memo.
It is shown ONE string - the Analyst's plain-text sentence - plus the provenance caveat,
and is told in the prompt that every number it writes must already appear there. The
Verifier then enforces that, because a prompt instruction is a request, not a guarantee.

If the model is unavailable, rate limited or overruled, the computed text is returned
unchanged. The product is never blocked on the model being up.
"""

from __future__ import annotations

from . import ai

SYSTEM = """\
You are a GIS analyst briefing an education NGO working in Pakistan.

Write 2-4 short sentences an officer could act on. Plain text only: no markdown, no
asterisks, no bullet points, no headings.

Hard rules:
- Use ONLY the facts given. Every number you write must appear verbatim in them.
- Never introduce a benchmark, a target, a cost, or a statistic that is not in the facts.
- Never present a child count as a precise census figure. They are modelled estimates
  from gridded population, and you must not imply otherwise.
- Distances are WALKING times, estimated in a straight line with a detour factor. Never
  describe them as driving times or as routed along roads.
- The caveat you are given about data completeness is not optional context. Reflect it.
- If no school is mapped in the district, say plainly that this reflects missing data
  rather than a confirmed absence of schools.
"""


def write(question: str, computed_text: str, caveat: str) -> tuple[str, str]:
    """Returns (brief, how) where how is "ai", "computed", or "computed (<reason>)"."""
    if not ai.available():
        return computed_text, "computed"
    try:
        brief = ai.chat(
            SYSTEM,
            f"Question: {question}\n\nFacts: {computed_text}\n\nData caveat: {caveat}",
            temperature=0.3,
        )
        return (brief or computed_text), ("ai" if brief else "computed")
    except Exception as exc:
        return computed_text, f"computed ({ai.error_reason(exc)})"
