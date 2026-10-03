"""
The five analyses ("building blocks") the AI can choose from.

The AI never computes anything. It only picks one of these functions and fills in its
parameters. Every number in an answer comes from the GeoPandas code below.

  find           list or count schools / facilities / hospitals, optionally within or beyond a
                 distance from facilities, schools, a map pin, the user, or the area centre
  coverage       the parts of the area farther than a distance from healthcare
  distance_grid  small squares across the area, coloured by distance to the nearest place
  summary        size, counts, densities, average and worst distance

All distance work happens in the area's UTM projection, where one unit is one metre.
Each function returns a plain dict: the facts (`text`) plus what the map should draw.
"""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box, mapping

from .data import Area

KIND_LABEL = {"schools": "schools", "facilities": "medical facilities", "hospitals": "hospitals"}
SING = {"schools": "school", "facilities": "medical facility", "hospitals": "hospital"}
NICE = [100, 200, 250, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 7500, 10000, 20000]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def fmt(metres: float) -> str:
    """1234.5 -> '1.2 km', 450 -> '450 m'."""
    if metres < 1000:
        return f"{round(metres)} m"
    return f"{metres / 1000:.1f} km" if metres < 10000 else f"{round(metres / 1000)} km"


def nice_up(value: float) -> int:
    return next((n for n in NICE if n >= value), math.ceil(value / 10000) * 10000)


def short_name(area: Area) -> str:
    return area.name.split(",")[0]


def name_of(row, kind: str) -> str:
    return row["name"] or f"an unnamed {SING.get(kind, 'place')}"


def features(area: Area, kind: str, projected: bool = True) -> gpd.GeoDataFrame:
    """Schools, all medical facilities, or only hospitals — in metres (projected) or lon/lat."""
    if kind == "schools":
        return area.schools_m if projected else area.schools
    gdf = area.facilities_m if projected else area.facilities
    if kind == "hospitals":
        gdf = gdf[gdf["category"].str.contains("hospital", case=False, na=False)]
    return gdf


def nearest(targets: gpd.GeoDataFrame, refs: gpd.GeoDataFrame, exclusive: bool = False):
    """
    For every target point, the distance (m) to its nearest reference point and which one it is.
    `exclusive=True` ignores a reference at exactly the same spot (a place compared to its own set).
    """
    if targets.empty or refs.empty:
        return None
    joined = gpd.sjoin_nearest(targets[["geometry"]], refs[["geometry"]], how="left",
                               distance_col="dist_m", exclusive=exclusive)
    return joined[~joined.index.duplicated(keep="first")]   # ties can repeat a target


def school_distances(area: Area) -> np.ndarray:
    j = nearest(area.schools_m, area.facilities_m)
    return np.sort(j["dist_m"].to_numpy()) if j is not None else np.array([])


def default_distance(area: Area) -> int:
    """A sensible radius for THIS area: the median school-to-facility distance, rounded up."""
    d = school_distances(area)
    return nice_up(max(float(np.median(d)), 150)) if len(d) else 1000


def lonlat(point_m: Point, area: Area) -> list[float]:
    p = gpd.GeoSeries([point_m], crs=area.utm).to_crs(4326).iloc[0]
    return [p.x, p.y]


def to_metres(lon: float, lat: float, area: Area) -> Point:
    return gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(area.utm).iloc[0]


# ---------------------------------------------------------------------------
# Where do we measure FROM?
# ---------------------------------------------------------------------------
def resolve_from(area: Area, frm: str, pin, me, notes: list[str]) -> dict:
    """
    Turn the `from` parameter into either a set of places or a single point.
    pin / me are [lon, lat] or None. `me` is only passed when the user is inside the area.
    """
    if frm in ("schools", "facilities", "hospitals"):
        refs = features(area, frm)
        if frm == "hospitals" and refs.empty:
            notes.append(f"No hospitals are mapped in {area.name}, so I measured from all medical facilities.")
            frm, refs = "facilities", features(area, "facilities")
        label = "a medical facility" if frm == "facilities" else f"a {SING[frm]}"
        return {"kind": "set", "from": frm, "refs": refs, "label": label}

    if frm == "me":
        if me:
            return {"kind": "point", "lonlat": me, "label": "your location"}
        # The app has already explained why `me` is missing (denied, or outside the area).
    if pin and frm != "centre":
        return {"kind": "point", "lonlat": pin, "label": "your map pin"}
    if frm == "pin":
        notes.append("No pin is set (click the map to drop one), so I used the centre of the area.")
    return {"kind": "point", "lonlat": list(area.centre()), "label": f"the centre of {short_name(area)}"}


# ---------------------------------------------------------------------------
# Block 1: find
# ---------------------------------------------------------------------------
def find(area: Area, p: dict, pin=None, me=None, notes: list[str] | None = None) -> dict:
    notes = list(notes or [])

    if p.get("target") == "all":
        ns, nf, nh = len(area.schools), len(area.facilities), len(features(area, "hospitals"))
        return {"op": "find", "target": "all",
                "text": f"{area.name} has {ns} schools and {nf} medical facilities ({nh} of them hospitals) "
                        f"mapped in {area.source} data. All of them are shown on the map."}

    target = p.get("target", "schools")
    T = features(area, target)
    if target == "hospitals" and T.empty:
        notes.append(f"No hospitals are mapped in {area.name}, so I used all medical facilities.")
        target, T = "facilities", features(area, "facilities")
    if T.empty:
        return {"op": "find", "target": target, "items": [], "text": f"No {KIND_LABEL[target]} are mapped in {area.name}."}

    ref = resolve_from(area, p.get("from") or ("facilities" if target == "schools" else "schools"), pin, me, notes)
    relation = p.get("relation", "all")
    d = None if relation == "all" else float(p.get("distance_m") or default_distance(area))
    T_ll = features(area, target, projected=False)

    # Distance from every target to the reference.
    if ref["kind"] == "point":
        origin = to_metres(ref["lonlat"][0], ref["lonlat"][1], area)
        dists = T.geometry.distance(origin)
        near_ll = {i: ref["lonlat"] for i in T.index}
        near_name = {}
    else:
        same_family = ({target, ref["from"]} <= {"facilities", "hospitals"}) or target == ref["from"]
        j = nearest(T, ref["refs"], exclusive=same_family)
        dists = j["dist_m"]
        refs_ll = features(area, ref["from"], projected=False)
        near_ll = {i: [refs_ll.geometry[r].x, refs_ll.geometry[r].y] for i, r in j["index_right"].items()}
        near_name = {i: name_of(refs_ll.loc[r], ref["from"]) for i, r in j["index_right"].items()}

    rows = [{"index": i, "dist": float(dists[i])} for i in T.index]
    if relation == "within":
        rows = [r for r in rows if r["dist"] <= d]
    elif relation == "beyond":
        rows = [r for r in rows if r["dist"] > d]
    sort = p.get("sort") or ("farthest" if relation == "beyond" else "nearest")
    rows.sort(key=lambda r: r["dist"], reverse=(sort == "farthest"))
    limit = max(1, min(int(p.get("limit") or 10), 25))

    items = []
    for r in rows:
        g = T_ll.geometry[r["index"]]
        items.append({"name": name_of(T_ll.loc[r["index"]], target), "lonlat": [g.x, g.y],
                      "dist": r["dist"], "near": near_ll.get(r["index"]), "near_name": near_name.get(r["index"])})
    shown = items[:limit]

    rel_text = (f"within {fmt(d)} of {ref['label']}" if relation == "within"
                else f"more than {fmt(d)} from {ref['label']}" if relation == "beyond" else "")
    if p.get("output") == "count":
        text = (f"{area.name} has {len(items)} {KIND_LABEL[target]}." if relation == "all"
                else f"{len(items)} of {len(T)} {KIND_LABEL[target]} in {area.name} are {rel_text}.")
    elif relation == "all" and limit == 1 and shown:
        i = shown[0]
        text = (f"The {sort} {SING[target]} to {ref['label']} is {i['name']}, {fmt(i['dist'])} away"
                + (f" (nearest: {i['near_name']})" if i["near_name"] else "") + ".")
    else:
        head = (f"{len(items)} {KIND_LABEL[target]} in {area.name}, ranked by distance to {ref['label']} ({sort} first)"
                if relation == "all" else f"{len(items)} of {len(T)} {KIND_LABEL[target]} are {rel_text}")
        listed = "; ".join(f"{i['name']} ({fmt(i['dist'])}" + (f", nearest: {i['near_name']}" if i["near_name"] else "") + ")"
                           for i in shown)
        more = f"; and {len(items) - len(shown)} more" if len(items) > len(shown) else ""
        text = f"{head}{': ' + listed if shown else ''}{more}."
    if notes:
        text += " " + " ".join(notes)

    return {"op": "find", "target": target, "relation": relation, "distance_m": d, "sort": sort,
            "ref": {"kind": ref["kind"], "label": ref["label"], "lonlat": ref.get("lonlat"),
                    "from": ref.get("from"),
                    "points": ([[g.x, g.y] for g in features(area, ref["from"], projected=False).geometry]
                               if ref["kind"] == "set" else None)},
            "items": items, "shown": shown, "text": text}


# ---------------------------------------------------------------------------
# Block 2: coverage (exact polygons: buffers -> union -> difference)
# ---------------------------------------------------------------------------
def coverage(area: Area, p: dict) -> dict:
    notes = []
    target = p.get("target") if p.get("target") in ("facilities", "hospitals") else "facilities"
    T = features(area, target)
    if target == "hospitals" and T.empty:
        notes.append("No hospitals are mapped, so I used all medical facilities.")
        target, T = "facilities", features(area, "facilities")
    d = float(p.get("distance_m") or default_distance(area))
    boundary = area.boundary_m.geometry.iloc[0]

    if T.empty:
        gap = boundary
    else:
        reach = T.buffer(d).union_all()            # everywhere within d of a facility
        gap = boundary.difference(reach)           # what's left of the area is the gap
    pct = round(gap.area / boundary.area * 100) if boundary.area else 0

    j = nearest(area.schools_m, T)
    in_gap_idx = [i for i, v in j["dist_m"].items() if v > d] if j is not None else list(area.schools.index)
    in_gap = [name_of(area.schools.loc[i], "schools") for i in in_gap_idx]

    if pct == 0 and not in_gap:
        text = f"Every part of {area.name} is within {fmt(d)} of a {SING[target]}, so there are no coverage gaps at that distance."
    else:
        listed = "; ".join(in_gap[:8]) + (f"; and {len(in_gap) - 8} more" if len(in_gap) > 8 else "")
        text = (f"About {pct}% of {area.name} ({gap.area / 1e6:.2f} km²) is more than {fmt(d)} from the nearest "
                f"{SING[target]} (shaded on the map). {len(in_gap)} of {len(area.schools)} schools are in those gaps"
                + (f": {listed}" if in_gap else "") + ".")
    if notes:
        text += " " + " ".join(notes)

    gap_ll = gpd.GeoSeries([gap], crs=area.utm).to_crs(4326).iloc[0]
    T_ll = features(area, target, projected=False)
    return {"op": "coverage", "target": target, "distance_m": d, "percent": pct,
            "gap": mapping(gap_ll) if not gap.is_empty else None,
            "targets": [[g.x, g.y] for g in T_ll.geometry],
            "schools_in_gap": [{"name": name_of(area.schools.loc[i], "schools"),
                                "lonlat": [area.schools.geometry[i].x, area.schools.geometry[i].y]} for i in in_gap_idx],
            "text": text}


# ---------------------------------------------------------------------------
# Block 3: distance grid
# ---------------------------------------------------------------------------
RAMP = ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027"]   # close (green) -> far (red)


def distance_grid(area: Area, p: dict) -> dict:
    target = p.get("target") or "schools"
    T = features(area, target)
    if T.empty:
        target, T = "facilities", features(area, "facilities")
    boundary = area.boundary_m.geometry.iloc[0]

    # About 600 squares whatever the area's size; at least 50 m wide.
    side = max(math.sqrt(boundary.area / 1e6 / 600), 0.05) * 1000
    minx, miny, maxx, maxy = boundary.bounds
    cells = [box(x, y, x + side, y + side) for x in np.arange(minx, maxx, side) for y in np.arange(miny, maxy, side)]
    grid = gpd.GeoDataFrame(geometry=cells, crs=area.utm)
    centres = grid.copy()
    centres["geometry"] = grid.centroid
    inside = centres.within(boundary)
    grid, centres = grid[inside], centres[inside]
    if grid.empty or T.empty:
        return {"op": "distance_grid", "target": target, "cells": [], "legend": [],
                "text": f"{area.name} is too small or has no {KIND_LABEL[target]} to build a distance map."}

    dist = nearest(centres, T)["dist_m"]
    q = lambda frac: float(np.quantile(dist, frac))
    breaks = sorted({nice_up(q(f)) for f in (0.2, 0.4, 0.6, 0.8)})
    cls = np.searchsorted(breaks, dist.to_numpy(), side="left")     # 0 = closest class

    grid_ll = grid.to_crs(4326)
    cell_list = [{"geometry": mapping(g), "cls": int(c)} for g, c in zip(grid_ll.geometry, cls)]
    legend = [{"color": RAMP[i], "label": f"≤ {fmt(b)}"} for i, b in enumerate(breaks)]
    legend.append({"color": RAMP[min(len(breaks), 4)], "label": f"> {fmt(breaks[-1])}"})
    text = (f"I split {area.name} into {len(grid)} squares of about {round(side)} m and measured the distance from "
            f"each to the nearest {SING[target]}. Half of the area is within {fmt(q(0.5))}, and the farthest square "
            f"is {fmt(float(dist.max()))} away. Greener squares are closer, redder squares farther.")
    return {"op": "distance_grid", "target": target, "cells": cell_list, "ramp": RAMP, "legend": legend, "text": text}


# ---------------------------------------------------------------------------
# Block 4: summary
# ---------------------------------------------------------------------------
def summary(area: Area, p: dict | None = None) -> dict:
    km2 = area.boundary_m.geometry.iloc[0].area / 1e6
    ns, nf, nh = len(area.schools), len(area.facilities), len(features(area, "hospitals"))
    worst = None
    j = nearest(area.schools_m, area.facilities_m)
    if j is not None:
        i = j["dist_m"].idxmax()
        r = j.loc[i, "index_right"]
        worst = {"name": name_of(area.schools.loc[i], "schools"),
                 "lonlat": [area.schools.geometry[i].x, area.schools.geometry[i].y],
                 "near": [area.facilities.geometry[r].x, area.facilities.geometry[r].y],
                 "dist": float(j.loc[i, "dist_m"])}
        avg = float(j["dist_m"].mean())
    else:
        avg = 0.0
    text = (f"{area.name} covers about {km2:.2f} km² with {ns} schools ({ns / km2:.1f} per km²) and {nf} medical "
            f"facilities, {nh} of them hospitals. That is {nf / ns:.1f} medical facilities per school; " if ns else
            f"{area.name} covers about {km2:.2f} km² with no mapped schools and {nf} medical facilities. ")
    if worst:
        text += f"a school is on average {fmt(avg)} from the nearest one, and the farthest is {worst['name']} at {fmt(worst['dist'])}. "
    text += "This data contains no official standard for how many schools or clinics an area should have."
    return {"op": "summary", "worst": worst, "text": text}


def run(area: Area, operation: str, params: dict, pin=None, me=None, notes=None) -> dict:
    if operation == "find":
        return find(area, params, pin=pin, me=me, notes=notes)
    if operation == "coverage":
        return coverage(area, params)
    if operation == "distance_grid":
        return distance_grid(area, params)
    return summary(area, params)
