"""
Suggested questions, built from the loaded area's real data.

A dense district gets a small radius (e.g. 250 m), a spread-out one a bigger radius,
so suggestions are always meaningful for the place on screen.
"""
from __future__ import annotations

from .analysis import default_distance, features, fmt, short_name, KIND_LABEL
from .data import Area

WELCOME = ["Which schools and clinics are near me?",
           "Which areas have poor access to healthcare?",
           "Which school has the worst access to healthcare?"]


def build(area: Area) -> list[str]:
    d = fmt(default_distance(area))
    has_hospitals = not features(area, "hospitals").empty
    return [
        f"Which schools are more than {d} from a medical facility?",
        "Which school has the worst access to healthcare?",
        f"Where are the healthcare coverage gaps at {d}?",
        (f"Which hospitals are nearest the centre of {short_name(area)}?" if has_hospitals
         else f"Which medical facilities are within {d} of schools?"),
        f"Are there enough schools and clinics in {short_name(area)}?",
    ]


def follow_ups(area: Area, result: dict) -> list[str]:
    d = fmt(result.get("distance_m") or default_distance(area))
    op, relation, target = result["op"], result.get("relation"), result.get("target", "schools")
    label = KIND_LABEL.get(target, "schools")
    if op == "find" and relation == "within":
        return [f"Which {label} are more than {d} away?", f"Show coverage gaps at {d}", f"Summarise access in {short_name(area)}"]
    if op == "find" and relation == "beyond":
        return [f"Which {label} are within {d}?", "Show a distance map to the nearest medical facility",
                "Which school has the worst access to healthcare?"]
    if op == "find":
        return ["Which areas have poor access to healthcare?", "Show a distance map to the nearest school",
                f"Summarise access in {short_name(area)}"]
    if op == "coverage":
        return [f"Which schools are more than {d} from a medical facility?",
                "Show a distance map to the nearest medical facility", "Which school has the worst access to healthcare?"]
    if op == "distance_grid":
        return ["Which areas have poor access to healthcare?", "Which schools are closest to medical facilities?",
                f"Summarise access in {short_name(area)}"]
    return ["Which schools are closest to medical facilities?", "Which areas have poor access to healthcare?",
            "How far is the nearest school from each neighbourhood?"]
