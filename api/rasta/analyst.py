"""
Agent 2 - the Analyst.  Deterministic, by design.

Every number in the system originates here, in ordinary Python over the precomputed
bundle. No language model is involved and none is consulted. That is not a limitation
we are apologising for: it is the reason a figure in the brief can be checked against
a figure in the analysis, which is what the Verifier does.

Each operation returns a dict with:
    facts  - machine-readable numbers
    text   - the same numbers as a plain sentence, which is the ONLY thing the writer sees
    draw   - optional map layers
"""

from __future__ import annotations

import math

from . import config


def _radius_m(minutes: float) -> float:
    return (minutes / 60.0) * (config.WALK_KMH * 1000.0) / config.DETOUR_FACTOR


def _local_scale(lats: list[float]) -> tuple[float, float]:
    lat0 = sum(lats) / len(lats) if lats else 25.0
    return 111_320.0 * math.cos(math.radians(lat0)), 110_540.0


def human_walk(minutes: float, no_supply: float = 999) -> str:
    """
    A walking time a person can picture.

    In a district with one mapped school the straight-line maths honestly produces
    figures like 1,511 minutes. That is arithmetically right and communicatively
    useless, so beyond a couple of hours we stop pretending the number is meaningful.
    """
    if minutes >= no_supply:
        return "no mapped school at all"
    if minutes >= 240:
        return "over four hours"
    if minutes >= 120:
        return f"about {minutes / 60:.0f} hours"
    return f"{minutes:.0f} minutes"


def _cohort_weight(bundle: dict, cohort: str) -> tuple[float, str]:
    """
    The equity weighting, applied on the demand side.

    No school dataset for Pakistan carries a gender field, so girls cannot be handled by
    filtering the supply. UNESCO's finding is about girls' attendance against walk time
    anyway, so the honest expression of it is a weight on the children who are far away.
    """
    if cohort != "girls":
        return 1.0, ""
    weight = config.GIRL_SHARE
    note = (
        f"Girls are counted as {round(config.GIRL_SHARE * 100)}% of children, and the "
        f"{round(config.GIRLS_PENALTY * 100)}% attendance gap UNESCO reports for girls "
        "walking 45-60 minutes is what makes the far-away group the priority."
    )
    return weight, note


def gap(bundle: dict, params: dict) -> dict:
    """How many children are beyond the walking threshold, and how far."""
    minutes = float(params.get("minutes") or config.THRESHOLD_MIN)
    cohort = params.get("cohort", "children")
    weight, cohort_note = _cohort_weight(bundle, cohort)
    cells = bundle["cells"]
    no_supply = bundle["params"].get("no_supply_min", 999)

    total = beyond = worst_min = 0.0
    unreachable = 0
    for i, m in enumerate(cells["min"]):
        ch = cells["ch"][i] * weight
        total += ch
        if m > minutes:
            beyond += ch
            worst_min = max(worst_min, min(m, no_supply))
        if m >= no_supply:
            unreachable += 1

    pct = round(100 * beyond / total) if total else 0
    label = "girls" if cohort == "girls" else "children"
    unmapped = unreachable == len(cells["min"]) and unreachable > 0

    text = (
        f"In {bundle['name']}, about {round(beyond):,} of {round(total):,} school-age "
        f"{label} ({pct}%) live more than {minutes:.0f} minutes' walk from the nearest "
        f"mapped school."
    )
    if unmapped:
        text += " No school is mapped anywhere in this district, so every cell counts as beyond."
    elif worst_min and worst_min < no_supply:
        text += f" The worst-served cells are about {human_walk(worst_min, no_supply)} away."
    if cohort_note:
        text += " " + cohort_note

    return {
        "op": "gap",
        "facts": {
            "district": bundle["name"], "threshold_min": minutes, "cohort": cohort,
            "children_total": round(total), "children_beyond": round(beyond),
            "percent_beyond": pct, "worst_minutes": round(worst_min),
            "fully_unmapped": unmapped,
        },
        "text": text,
    }


def shortlist(bundle: dict, params: dict) -> dict:
    """The ranked sites to field-verify, straight from the precomputed bundle."""
    limit = int(params.get("limit") or 5)
    no_supply = bundle["params"].get("no_supply_min", 999)
    cohort = params.get("cohort", "children")
    weight, cohort_note = _cohort_weight(bundle, cohort)
    intervention = params.get("intervention", "any")

    sites = []
    for i, s in enumerate(bundle["shortlist"][:limit], start=1):
        sites.append({
            "rank": i,
            "lon": s["lon"], "lat": s["lat"],
            "reached": round(s["children_reached"] * weight),
            "walk_min": s["walk_min_to_nearest"],
            "intervention": intervention if intervention != "any" else s.get("intervention", "ncl"),
        })

    label = "girls" if cohort == "girls" else "children"
    if not sites:
        text = f"No underserved cells were found in {bundle['name']} at this threshold."
    else:
        total = sum(s["reached"] for s in sites)
        lead = sites[0]
        text = (
            f"The top {len(sites)} sites in {bundle['name']} would together put about "
            f"{total:,} currently underserved {label} within a "
            f"{bundle['params']['threshold_min']}-minute walk. The strongest single site "
            f"reaches about {lead['reached']:,} {label}, in an area whose nearest mapped "
            f"school is {human_walk(lead['walk_min'], no_supply)} away."
        )
        if cohort_note:
            text += " " + cohort_note

    return {
        "op": "shortlist",
        "facts": {
            "district": bundle["name"], "cohort": cohort, "count": len(sites),
            "children_reached_total": sum(s["reached"] for s in sites),
            "top_reached": sites[0]["reached"] if sites else 0,
            "sites": sites,
        },
        "text": text,
        "draw": {"sites": sites},
    }


def summary(bundle: dict, params: dict) -> dict:
    t, c = bundle["totals"], bundle["confidence"]
    pct = round(100 * t["children_underserved_est"] / t["children_est"]) if t["children_est"] else 0
    text = (
        f"{bundle['name']} has about {t['population']:,} people and an estimated "
        f"{t['children_est']:,} school-age children across {t['hexes']:,} population cells. "
        f"{c['mapped']:,} schools are mapped in open data. About "
        f"{t['children_underserved_est']:,} children ({pct}%) are more than "
        f"{bundle['params']['threshold_min']} minutes' walk from one."
    )
    return {
        "op": "summary",
        "facts": {
            "district": bundle["name"], "population": t["population"],
            "children_est": t["children_est"], "cells": t["hexes"],
            "mapped_schools": c["mapped"],
            "children_beyond": t["children_underserved_est"], "percent_beyond": pct,
        },
        "text": text,
    }


def compare(bundles: list[dict], params: dict) -> dict:
    """Set districts side by side on the one metric that matters."""
    rows = []
    for b in bundles:
        t = b["totals"]
        pct = round(100 * t["children_underserved_est"] / t["children_est"]) if t["children_est"] else 0
        rows.append({
            "district": b["name"], "mapped": b["confidence"]["mapped"],
            "children": t["children_est"], "beyond": t["children_underserved_est"],
            "percent": pct,
        })
    rows.sort(key=lambda r: -r["percent"])
    parts = [f"{r['district']}: {r['percent']}% of {r['children']:,} children beyond the "
             f"threshold, {r['mapped']:,} schools mapped" for r in rows]
    return {
        "op": "compare",
        "facts": {"rows": rows},
        "text": "Comparing districts - " + "; ".join(parts) + ".",
    }


def run(operation: str, bundle, params: dict) -> dict:
    if operation == "compare" and isinstance(bundle, list):
        return compare(bundle, params)
    if isinstance(bundle, list):
        bundle = bundle[0]
    if operation == "gap":
        return gap(bundle, params)
    if operation == "shortlist":
        return shortlist(bundle, params)
    # Anything unrecognised falls through to a summary rather than failing: a defensive
    # default inherited from the forked code, and still the right behaviour.
    return summary(bundle, params)
