"""
Agent 5 - the Verifier.  Deterministic, and able to overrule the model.

Every number in a generated brief must trace back to a number the Analyst computed. The
Verifier pulls the numerals out of the prose, compares them against the facts the writer
was given, and if any of them cannot be accounted for the brief is discarded and the
computed text is returned instead.

The difficult half of this is NOT catching hallucinations - a naive regex does that. It
is accepting the many legitimate ways prose restates a number: "3,100" and "3100",
"15 minutes" and "15-minute", "11%" and "11 %", and rounding 553,751 to "about 554,000".
A verifier that rejects good answers gets switched off within a day, so the tolerance
below is as important as the check itself.
"""

from __future__ import annotations

import re

# Matches 1,234.5 / 1234 / 0.42 / 85% - the leading boundary keeps us off the digits
# inside words like "COVID19".
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)")

RELATIVE_TOLERANCE = 0.01     # 1%: enough for honest rounding, far too tight to hide an error
ABSOLUTE_TOLERANCE = 0.5      # so small integers compare sensibly


def _numbers(text: str) -> list[float]:
    out = []
    for token in NUMBER.findall(text or ""):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            pass
    return out


def _allowed(facts, text: str) -> set[float]:
    """
    Every number the writer was legitimately shown: the computed facts, and the computed
    sentence. Nothing else is permissible in the brief.
    """
    allowed: set[float] = set()

    def walk(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            allowed.add(float(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            allowed.update(_numbers(value))

    walk(facts)
    allowed.update(_numbers(text))

    # Percentages are routinely restated as their fraction and vice versa, and a total
    # is routinely given in thousands. Admitting these explicitly is what keeps the
    # check from firing on correct prose.
    for value in list(allowed):
        if value:
            allowed.add(round(value / 1000.0, 1))
            allowed.add(round(value * 100.0, 1))
            allowed.add(round(value / 100.0, 4))
    return allowed


def _accounted(value: float, allowed: set[float]) -> bool:
    for a in allowed:
        if abs(value - a) <= max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(a)):
            return True
    return False


def verify(brief: str, facts, computed_text: str) -> dict:
    """
    Returns {"ok": bool, "unsupported": [...], "checked": int}.

    ok=False means the brief asserted a number that the analysis does not support, and
    the caller must not show it.
    """
    allowed = _allowed(facts, computed_text)
    unsupported = [v for v in _numbers(brief) if not _accounted(v, allowed)]
    return {
        "ok": not unsupported,
        "unsupported": sorted(set(unsupported))[:8],
        "checked": len(_numbers(brief)),
    }
