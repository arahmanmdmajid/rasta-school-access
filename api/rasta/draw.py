"""
Turn an analysis result into map layers for the web page.

The page doesn't know any GIS. It receives a list of GeoJSON layers, each tagged with a
ROLE that says what it means ("buffer", "hit_within", "gap", ...). The page only decides
which colour each role gets in light or dark mode, so the look stays consistent.
"""
from __future__ import annotations

from .analysis import fmt


def _point(lonlat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": lonlat}, "properties": props}


def _line(a, b, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [a, b]}, "properties": props}


def _layer(role, features, **extra):
    return {"role": role, "geojson": {"type": "FeatureCollection", "features": features}, **extra}


def layers(result: dict) -> dict:
    """Returns {"layers": [...], "fit": "boundary" | "results", "legend": [...]}."""
    op = result["op"]

    if op == "find":
        if result.get("target") == "all" or not result.get("items") and not result.get("ref"):
            return {"layers": [], "fit": "boundary", "legend": []}
        out = []
        ref, d = result["ref"], result.get("distance_m")
        if ref["kind"] == "point":
            out.append(_layer("ref_point", [_point(ref["lonlat"], label=ref["label"])]))
            if d:
                out.append(_layer("ref_circle", [_point(ref["lonlat"], radius=d)]))
        elif d and ref.get("points") and len(ref["points"]) <= 150:
            out.append(_layer("buffer", [_point(p, radius=d) for p in ref["points"]]))
        role = {"within": "hit_within", "beyond": "hit_beyond"}.get(result["relation"], "hit_all")
        out.append(_layer(role, [_point(i["lonlat"], label=f"{i['name']} — {fmt(i['dist'])}") for i in result["items"]]))
        out.append(_layer("line", [_line(i["lonlat"], i["near"], relation=result["relation"])
                                   for i in result["shown"] if i.get("near")], relation=result["relation"]))
        return {"layers": out, "fit": "results" if result["items"] else "boundary", "legend": []}

    if op == "coverage":
        out = []
        if result["gap"]:
            out.append(_layer("gap", [{"type": "Feature", "geometry": result["gap"], "properties": {}}]))
        if len(result["targets"]) <= 150:
            out.append(_layer("buffer", [_point(p, radius=result["distance_m"]) for p in result["targets"]]))
        out.append(_layer("hit_gap", [_point(s["lonlat"], label=f"{s['name']} — in a coverage gap")
                                      for s in result["schools_in_gap"]]))
        label = f"More than {fmt(result['distance_m'])} from a {'hospital' if result['target'] == 'hospitals' else 'medical facility'}"
        return {"layers": out, "fit": "boundary", "legend": [{"role": "gap", "label": label}]}

    if op == "distance_grid":
        cells = [{"type": "Feature", "geometry": c["geometry"], "properties": {"cls": c["cls"]}} for c in result["cells"]]
        return {"layers": [_layer("grid", cells, ramp=result.get("ramp", []))], "fit": "boundary",
                "legend": [{"label": f"Distance to nearest {result['target'].rstrip('s').replace('facilitie', 'facility')}"}]
                          + result["legend"]}

    worst = result.get("worst")
    if not worst:
        return {"layers": [], "fit": "boundary", "legend": []}
    return {"layers": [_layer("line", [_line(worst["lonlat"], worst["near"])], relation="beyond"),
                       _layer("worst", [_point(worst["lonlat"], label=f"{worst['name']} — farthest from healthcare ({fmt(worst['dist'])})")])],
            "fit": "boundary", "legend": []}
