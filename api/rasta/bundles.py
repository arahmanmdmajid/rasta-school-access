"""
Loading district bundles, with a provenance gate.

Every bundle carries how many schools are actually mapped in it. That number is attached
to everything downstream and travels all the way into the written brief, so a district
with almost no mapped schools cannot quietly produce a confident-sounding answer.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "web" / "districts"



@lru_cache(maxsize=1)
def index() -> dict:
    path = DIR / "index.json"
    if not path.exists():
        return {"districts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def codes() -> list[str]:
    return [d["code"] for d in index().get("districts", [])]


def names() -> list[str]:
    return [d["name"] for d in index().get("districts", [])]


def resolve(text: str | None) -> str | None:
    """Match a district name or code the user typed against what is actually bundled."""
    if not text:
        return None
    needle = text.strip().lower()
    for d in index().get("districts", []):
        if needle == d["code"].lower() or needle == d["name"].lower():
            return d["code"]
    for d in index().get("districts", []):
        if needle in d["name"].lower() or d["name"].lower() in needle:
            return d["code"]
    return None


@lru_cache(maxsize=4)
def load(code: str) -> dict | None:
    """Bundles run to ~500 KB, so a handful in memory is plenty."""
    path = DIR / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def provenance(bundle: dict) -> dict:
    """
    The gate. Returns the confidence facts that must accompany any claim made about
    this district, including a one-line caveat written in plain language.
    """
    c = bundle.get("confidence", {})
    mapped = c.get("mapped", 0)
    ratio = c.get("province_ratio")
    verdict = "none" if mapped == 0 else "low" if (ratio is None or ratio < 0.25) else "fair"
    if mapped == 0:
        caveat = (
            f"No schools at all are mapped in {bundle['name']} in the open data used here, "
            "so every settlement appears unserved. That is a statement about the map, not "
            "about the district."
        )
    else:
        pct = f"about {round(ratio * 100)}%" if ratio is not None else "a small fraction"
        caveat = (
            f"Only {mapped:,} schools are mapped in {bundle['name']}. Across "
            f"{bundle['province']}, open data holds {pct} of the schools the official count "
            "reports, so these are places to field-verify rather than confirmed gaps."
        )
    return {"mapped": mapped, "ratio": ratio, "verdict": verdict, "caveat": caveat}
