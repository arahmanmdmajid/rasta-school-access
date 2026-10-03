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

# Matches 1,234.5 / 1234 / 0.42 / 85%, capturing any unit that follows. The leading
# boundary keeps us off the digits inside words like "COVID19".
NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(%|percent|minutes?|mins?|hours?|km|kilomet\w*|met(?:re|er)s?)?",
    re.I,
)

RELATIVE_TOLERANCE = 0.01     # 1%: enough for honest rounding, far too tight to hide an error
ABSOLUTE_TOLERANCE = 0.5      # so small integers compare sensibly

# A bare integer this small, with no unit attached, is a list marker, an ordinal or a
# count of sites - not a claim about children, distance or coverage. Measured against
# real model output, these were the only false rejections, and flagging them would have
# been enough to get the whole check switched off. A small number WITH a unit ("3%",
# "3 minutes") is a claim, and is still checked.
BENIGN_INTEGER_MAX = 10


def _tokens(text: str) -> list[tuple[float, str]]:
    out = []
    for raw, unit in NUMBER.findall(text or ""):
        try:
            out.append((float(raw.replace(",", "")), (unit or "").lower()))
        except ValueError:
            pass
    return out


def _numbers(text: str) -> list[float]:
    return [value for value, _ in _tokens(text)]


def _allowed(facts, text: str, caveat: str = "") -> set[float]:
    """
    Every number the writer was legitimately shown.

    That means the computed facts, the computed sentence, AND the provenance caveat.
    The caveat is easy to forget and was: the writer is handed it and told to reflect
    it, so "only 21 schools are mapped, roughly 3% of the official count" is the writer
    doing exactly as instructed. Leaving the caveat out of this set made the verifier
    punish correct behaviour, which is worse than not having one.
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
    allowed.update(_numbers(caveat))

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


def verify(brief: str, facts, computed_text: str, caveat: str = "") -> dict:
    """
    Returns {"ok": bool, "unsupported": [...], "checked": int}.

    ok=False means the brief asserted a number that the analysis does not support, and
    the caller must not show it.
    """
    allowed = _allowed(facts, computed_text, caveat)
    tokens = _tokens(brief)
    unsupported = [
        value for value, unit in tokens
        if not _accounted(value, allowed)
        and not (not unit and float(value).is_integer() and value <= BENIGN_INTEGER_MAX)
    ]
    return {
        "ok": not unsupported,
        "unsupported": sorted(set(unsupported))[:8],
        "checked": len(tokens),
    }
