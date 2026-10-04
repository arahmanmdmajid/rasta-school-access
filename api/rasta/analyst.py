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
        # The map should show what the sentence is talking about, so the worst-served
        # cells come back as coordinates for the page to outline.
        "draw": {"cells": _worst_cells(bundle, minutes), "label": "worst-served cells"},
    }


def _worst_cells(bundle: dict, minutes: float, limit: int = 40) -> list[list[float]]:
    """The cells furthest from a school, as [lon, lat] for the page to highlight."""
    cells = bundle["cells"]
    rows = [(cells["min"][i], cells["ch"][i], i) for i in range(len(cells["min"]))
            if cells["min"][i] > minutes]
    # Worst first, but weighted by how many children are actually there: an empty cell an
    # hour from school matters less than a crowded one forty minutes away.
    rows.sort(key=lambda r: -(r[0] * (r[1] ** 0.5)))
    return [[cells["lon"][i], cells["lat"][i]] for _, _, i in rows[:limit]]


def poorest(bundle: dict, params: dict) -> dict:
    """
    Where the least-privileged underserved children are.

    This is the one question the Relative Wealth Index is actually for, and it is what an
    officer means by "show me the least privileged area". RWI is a modelled estimate of
    wealth RELATIVE to the rest of the country, so it ranks places - it never says anyone
    is poor in absolute terms, and the wording has to keep that distinction.
    """
    minutes = float(params.get("minutes") or config.THRESHOLD_MIN)
    limit = int(params.get("limit") or 25)
    cells = bundle["cells"]

    rows = [(cells["rwi"][i], cells["ch"][i], cells["min"][i], i)
            for i in range(len(cells["min"]))
            if cells["rwi"][i] is not None and cells["min"][i] > minutes]
    if not rows:
        return {
            "op": "poorest",
            "facts": {"district": bundle["name"], "count": 0},
            "text": (f"No cell in {bundle['name']} is both beyond {minutes:.0f} minutes' "
                     f"walk and carries a relative wealth estimate."),
        }

    rows.sort(key=lambda r: r[0])                 # lowest relative wealth first
    picked = rows[:limit]
    children = sum(r[1] for r in picked)
    worst_rwi = picked[0][0]
    median_rwi = sorted(c for c in cells["rwi"] if c is not None)[len(
        [c for c in cells["rwi"] if c is not None]) // 2]

    text = (
        f"The least well-off underserved areas of {bundle['name']} are the {len(picked)} "
        f"cells with the lowest relative wealth that are also beyond {minutes:.0f} "
        f"minutes' walk of a mapped school. About {round(children):,} school-age children "
        f"live in them. Their relative wealth runs down to {worst_rwi:.2f}, against a "
        f"district median of {median_rwi:.2f}. Relative wealth is a modelled estimate of "
        f"standing compared with the rest of Pakistan, not a measure of income, so it "
        f"ranks places rather than identifying poor households."
    )
    return {
        "op": "poorest",
        "facts": {
            "district": bundle["name"], "count": len(picked),
            "children": round(children), "lowest_rwi": round(worst_rwi, 2),
            "median_rwi": round(median_rwi, 2), "threshold_min": minutes,
        },
        "text": text,
        "draw": {"cells": [[cells["lon"][r[3]], cells["lat"][r[3]]] for r in picked],
                 "label": "least well-off underserved cells"},
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


def explain(bundle: dict, params: dict) -> dict:
    """
    Questions about the tool rather than about a district.

    These are the first things anyone asks - what am I looking at, what do the colours
    mean, how accurate is this - and refusing them made the assistant look broken. The
    answers are assembled from config and from the bundle, not written by the model, so
    the Verifier can still check every number in the prose that follows.
    """
    from . import bundles as _bundles          # local: keeps the import graph one-way

    topic = params.get("topic", "overview")
    c, t, p = bundle["confidence"], bundle["totals"], bundle["params"]
    bands = ", ".join(f"{b} min" for b in p["bands_min"])
    pct = round(100 * t["children_underserved_est"] / t["children_est"]) if t["children_est"] else 0

    if topic == "colours":
        text = (
            f"Each cell on the map is a patch of population, coloured by how long it "
            f"takes to walk to the nearest mapped school. The bands are {bands}, from "
            f"green for under {p['threshold_min']} minutes to red beyond an hour. Dark "
            f"dots are the {c['mapped']:,} schools that exist in open data for "
            f"{bundle['name']}."
        )
    elif topic == "method":
        text = (
            f"Walking time is estimated, not routed. The straight-line distance to the "
            f"nearest mapped school is multiplied by a detour factor of "
            f"{config.DETOUR_FACTOR} and divided by a walking speed of "
            f"{config.WALK_KMH} km/h, so {p['threshold_min']} minutes works out at about "
            f"{round(p['radius_m'])} metres. The {p['threshold_min']}-minute threshold is "
            f"UNESCO's: girls aged 10 to 12 who walk 45 to 60 minutes are 15 percent less "
            f"likely to attend than those within {p['threshold_min']}. Because the model "
            f"is closed form a catchment is a circle, which is what lets it follow the "
            f"cursor with no server call."
        )
    elif topic == "data":
        names = ", ".join(s["name"] for s in config.SOURCES.values())
        text = (
            f"Schools come from UNICEF Giga and Overture Maps, merged and de-duplicated "
            f"at 75 metres. Population is Kontur's gridded dataset, relative wealth is "
            f"Meta's index, and district boundaries are OCHA's. In full: {names}. "
            f"Everything is open data and every source is credited on the page."
        )
    elif topic == "limits":
        ratio = c.get("province_ratio")
        share = f"about {round(ratio * 100)} percent" if ratio else "a small share"
        text = (
            f"The honest answer is that the map is incomplete. {c['mapped']:,} schools "
            f"are mapped in {bundle['name']}, and across {bundle['province']} open data "
            f"holds {share} of the schools the official count reports. Distances are "
            f"straight-line estimates rather than routed along roads, and child counts "
            f"apply a national age share of {config.CHILD_SHARE} to gridded population, "
            f"so they are modelled estimates and not census figures. Nothing here says a "
            f"school is open or functioning, only that it is on the map. Treat every "
            f"result as a place to field-verify."
        )
    elif topic == "coverage":
        codes = _bundles.index().get("districts", [])
        text = (
            f"{len(codes)} districts of {bundle['province']} are loaded here, covering "
            f"every district in the province. The pipeline itself runs on any of "
            f"Pakistan's 160 districts; these are the ones precomputed for this build, "
            f"which is why the picker lists them and nothing else."
        )
    elif topic == "ai":
        text = (
            "Five agents answer each question, and two of them are language models. A "
            "planner reads the question and chooses an analysis; the analysis itself is "
            "ordinary Python, so every number is computed rather than generated. A writer "
            "turns those numbers into prose, and a verifier then checks every figure in "
            "that prose against what was computed and discards the wording if anything "
            "does not match. The model chooses; the code computes."
        )
    elif topic == "scope":
        text = (
            "The same pipeline works for any facility people walk to. Schools are the "
            "configured type here, and switching it to health facilities changes the "
            "source query and nothing else - the population grid, the walking model, the "
            "ranking and the verifier are all unchanged."
        )
    else:
        text = (
            f"This map shows how far children have to walk to school. You are looking at "
            f"{bundle['name']} in {bundle['province']}: about {t['children_est']:,} "
            f"school-age children across {t['hexes']:,} population cells, with "
            f"{c['mapped']:,} schools visible in open data. Around "
            f"{t['children_underserved_est']:,} of those children ({pct} percent) live "
            f"more than {p['threshold_min']} minutes' walk from one. Move the cursor over "
            f"the map and the circle is a {p['threshold_min']}-minute walk, reading out "
            f"how many underserved children a new centre there would reach."
        )

    return {
        "op": "explain",
        "facts": {
            "topic": topic, "district": bundle["name"], "province": bundle["province"],
            "mapped_schools": c["mapped"], "threshold_min": p["threshold_min"],
            "radius_m": round(p["radius_m"]), "children_est": t["children_est"],
            "children_beyond": t["children_underserved_est"], "percent_beyond": pct,
            "cells": t["hexes"],
        },
        "text": text,
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
    if operation == "explain":
        return explain(bundle, params)
    if operation == "poorest":
        return poorest(bundle, params)
    # Anything unrecognised falls through to a summary rather than failing: a defensive
    # default inherited from the forked code, and still the right behaviour.
    return summary(bundle, params)
