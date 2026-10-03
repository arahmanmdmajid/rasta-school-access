"""
Build a self-contained district bundle: everything the map needs, precomputed.

The bundle IS the response. Each district becomes one JSON file that ships to the
static site alongside the page, so the map, the hover readout, the choropleth and the
shortlist need no server call at all - which also means a sleeping free-tier backend
cannot break the demo.

    python scripts/build_bundles.py --district PK714
    python scripts/build_bundles.py --province Sindh
    python scripts/build_bundles.py --district PK726 --facility-type health

Build time only. Outputs to data/districts/<ADM2_PCODE>.json.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from rasta import config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "web" / "districts"      # beside the page: one location, served statically

ADMIN2 = f"/vsizip/{RAW / 'pak_admin_boundaries.shp.zip'}/pak_admin2.shp"
KONTUR = RAW / "kontur_population_PK_20231101.gpkg"
RWI_CSV = RAW / "ind_pak_relative_wealth_index.csv"
GIGA = RAW / "giga_pak.json"
OVERTURE = RAW / "overture_pak_schools.json"

MAX_BYTES = 900_000          # keep a district under ~1 MB so the page loads fast
SIMPLIFY_M = 100             # metres, applied in UTM before writing render polygons
MIN_POP_PER_HEX = 1          # a hexagon with nobody in it is not demand
NO_SUPPLY_MIN = 999          # minutes recorded when no mapped school exists at all
CANDIDATE_CAP = 600          # densest underserved cells considered as shortlist sites
DEDUPE_M = 75                # two points this close are the same school in both sources

# Official school counts, for the completeness ratio. Only districts with a cited
# figure get a ratio; the rest report "mapped" alone rather than inventing a
# denominator. Source: Pakistan Institute of Education, Pakistan Education Statistics.
OFFICIAL_SCHOOLS: dict[str, int] = {}
OFFICIAL_PROVINCE = {"Sindh": 48000}          # PIE, Pakistan Education Statistics
# What the merged Giga + Overture extract actually yields for the province, counted from
# the built bundles. Measured 2026-10-03 on one bbox for every source so the comparison is
# like for like: OpenStreetMap 1,571, UNICEF Giga 1,557 (OSM-derived), Overture 6,042.
PROVINCE_MAPPED = {"Sindh": 6390}


# ----------------------------------------------------------------------------- load

def load_district(code: str) -> gpd.GeoDataFrame:
    admin = gpd.read_file(ADMIN2)
    row = admin[admin["adm2_pcode"] == code]
    if row.empty:
        sys.exit(f"unknown district code {code}. Try --list to see them.")
    return row.reset_index(drop=True)


def _giga_points() -> gpd.GeoDataFrame:
    if not GIGA.exists():
        return gpd.GeoDataFrame({"name": [], "level": [], "source": []}, geometry=[], crs=4326)
    df = pd.DataFrame(json.loads(GIGA.read_text(encoding="utf-8")))
    df = df[pd.to_numeric(df["latitude"], errors="coerce").notna()]
    return gpd.GeoDataFrame(
        {"name": df["school_name"].fillna("").astype(str),
         "level": df["education_level"].fillna("Unknown").astype(str),
         "source": "giga"},
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=4326)


def _overture_points() -> gpd.GeoDataFrame:
    if not OVERTURE.exists():
        return gpd.GeoDataFrame({"name": [], "level": [], "source": []}, geometry=[], crs=4326)
    df = pd.DataFrame(json.loads(OVERTURE.read_text(encoding="utf-8")))
    if df.empty:
        return gpd.GeoDataFrame({"name": [], "level": [], "source": []}, geometry=[], crs=4326)
    return gpd.GeoDataFrame(
        {"name": df["name"].fillna("").astype(str),
         "level": df["category"].fillna("Unknown").astype(str),
         "source": "overture"},
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=4326)


def load_schools(boundary: gpd.GeoDataFrame, utm) -> gpd.GeoDataFrame:
    """
    Schools inside the district, from both open sources, de-duplicated.

    Using both is worth the trouble because they are genuinely independent. UNICEF Giga
    turned out to be OpenStreetMap-derived for Pakistan (1,557 for Sindh against OSM's
    1,571, and 2 in Tharparkar either way), whereas Overture's Pakistan schools come
    overwhelmingly from Meta - 6,042 for Sindh and 228 in Tharparkar. Neither is
    complete; together they are measurably less incomplete.

    Two points within DEDUPE_M of each other are treated as the same school, with the
    Giga record kept so the education_level field survives.
    """
    parts = [p for p in (_giga_points(), _overture_points()) if len(p)]
    if not parts:
        sys.exit("no school data - run scripts/fetch_giga.py and scripts/fetch_overture.py")

    inside = []
    for part in parts:
        hit = gpd.sjoin(part, boundary[["geometry"]], predicate="within")
        inside.append(hit.drop(columns=["index_right"]))

    giga = inside[0].to_crs(utm).reset_index(drop=True)
    if len(inside) < 2 or inside[1].empty:
        return giga.to_crs(4326)

    extra = inside[1].to_crs(utm).reset_index(drop=True)
    if not giga.empty:
        joined = gpd.sjoin_nearest(extra, giga[["geometry"]], how="left",
                                   max_distance=DEDUPE_M, distance_col="_d")
        joined = joined[~joined.index.duplicated(keep="first")]
        extra = extra[joined["index_right"].isna().to_numpy()]

    merged = pd.concat([giga, extra], ignore_index=True)
    return gpd.GeoDataFrame(merged, geometry="geometry", crs=utm).to_crs(4326)


def load_demand(boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Kontur population hexes clipped to the district. Kontur ships in EPSG:3857."""
    if not KONTUR.exists():
        sys.exit(f"{KONTUR} missing - run scripts/fetch_data.py first")
    bbox = tuple(boundary.to_crs(3857).total_bounds)
    hexes = gpd.read_file(KONTUR, bbox=bbox).to_crs(4326)
    inside = gpd.sjoin(hexes, boundary[["geometry"]], predicate="intersects")
    inside = inside.drop(columns=["index_right"])
    # Hexes with no people are not demand. Dropping them is both correct modelling and
    # the difference between a 1.3 MB bundle and a 300 KB one in a desert district like
    # Tharparkar, where most of the area is genuinely uninhabited.
    inside = inside[inside["population"] >= MIN_POP_PER_HEX]
    return inside.reset_index(drop=True)


def attach_rwi(demand: gpd.GeoDataFrame, utm) -> gpd.GeoDataFrame:
    """Nearest Relative Wealth Index point to each hexagon centroid."""
    minx, miny, maxx, maxy = demand.total_bounds
    pad = 0.1
    rwi = pd.read_csv(RWI_CSV)
    rwi = rwi[
        rwi.longitude.between(minx - pad, maxx + pad)
        & rwi.latitude.between(miny - pad, maxy + pad)
    ]
    if rwi.empty:
        demand["rwi"] = np.nan
        return demand

    pts = gpd.GeoDataFrame(
        rwi[["rwi"]].reset_index(drop=True),
        geometry=gpd.points_from_xy(rwi.longitude, rwi.latitude),
        crs=4326,
    ).to_crs(utm)

    centroids = gpd.GeoDataFrame(geometry=demand.to_crs(utm).centroid, crs=utm)
    joined = gpd.sjoin_nearest(centroids, pts, how="left")
    joined = joined[~joined.index.duplicated(keep="first")]
    demand["rwi"] = joined["rwi"].to_numpy()
    return demand


# ------------------------------------------------------------------------- analysis

def walk_minutes_to_supply(demand: gpd.GeoDataFrame, supply: gpd.GeoDataFrame, utm):
    """
    Straight-line metres from each hexagon centroid to the nearest mapped school.

    When a district has no mapped school at all - which happens, Dadu has a population
    over a million and zero schools in open data - every cell is beyond every walking
    threshold. That is encoded as a large finite sentinel rather than infinity, because
    infinity silently dropped out of the "underserved" mask while still counting in the
    shortlist, and the two disagreed.
    """
    centroids = gpd.GeoDataFrame(geometry=demand.to_crs(utm).centroid, crs=utm)
    if supply.empty:
        return (np.full(len(demand), np.nan),
                np.full(len(demand), float(NO_SUPPLY_MIN)))

    joined = gpd.sjoin_nearest(centroids, supply.to_crs(utm)[["geometry"]],
                               how="left", distance_col="dist_m")
    joined = joined[~joined.index.duplicated(keep="first")]
    dist = joined["dist_m"].to_numpy(dtype=float)
    minutes = np.array([config.walk_minutes(d) for d in dist])
    # Any cell that somehow failed to match is treated the same way: unreachable.
    minutes = np.where(np.isfinite(minutes), minutes, float(NO_SUPPLY_MIN))
    return dist, minutes


def _hex_template(demand: gpd.GeoDataFrame, utm) -> list[list[float]]:
    """
    One hexagon, as six vertex offsets in metres from its own centre.

    Every Kontur cell is the same H3 hexagon to within a few metres, so the browser can
    rebuild all of them from the centroids it already has. Offsets are sorted by angle so
    the ring is drawn in order.
    """
    sample = demand.to_crs(utm).iloc[:2000]
    rings = []
    for geom, centre in zip(sample.geometry, sample.geometry.centroid):
        coords = np.asarray(geom.exterior.coords[:-1])
        if len(coords) != 6:
            continue
        offsets = coords - np.array([centre.x, centre.y])
        rings.append(offsets[np.argsort(np.arctan2(offsets[:, 1], offsets[:, 0]))])
    if not rings:
        return []
    return [[round(float(x), 1), round(float(y), 1)] for x, y in np.mean(rings, axis=0)]


def shortlist(lon, lat, children, minutes, limit=10):
    """
    Greedy ranked sites to field-verify.

    A candidate is scored by the currently-underserved children within one walking
    catchment of it. Chosen sites are then spatially de-duplicated by that same radius,
    so the list is ten different places rather than ten views of one village.
    """
    radius = config.walk_radius_m()
    underserved = minutes > config.THRESHOLD_MIN
    if not underserved.any():
        return []

    # Local metre scaling; good to better than 0.1% at these latitudes.
    lat0 = float(np.mean(lat))
    kx = 111_320.0 * np.cos(np.radians(lat0))
    ky = 110_540.0
    x, y = lon * kx, lat * ky

    # Evaluating every underserved cell as a candidate is O(candidates x cells) per pick,
    # which is minutes of CPU in a district like Tharparkar with 8,500 cells. The best
    # site always sits on or beside a cell with many children, so only the densest
    # candidates are worth scoring - and that turns minutes into under a second.
    order = np.argsort(children)[::-1]
    idx = np.array([i for i in order if underserved[i]][:CANDIDATE_CAP])
    picked, used = [], np.zeros(len(lon), dtype=bool)

    for _ in range(limit):
        best, best_score = -1, 0.0
        for i in idx:
            if used[i]:
                continue
            near = ((x - x[i]) ** 2 + (y - y[i]) ** 2) <= radius ** 2
            score = float(children[near & underserved & ~used].sum())
            if score > best_score:
                best, best_score = i, score
        if best < 0 or best_score <= 0:
            break

        near = ((x - x[best]) ** 2 + (y - y[best]) ** 2) <= radius ** 2
        used |= near                      # never count the same children twice
        picked.append({
            "lon": round(float(lon[best]), 5),
            "lat": round(float(lat[best]), 5),
            "children_reached": int(round(best_score)),
            "walk_min_to_nearest": round(float(minutes[best]), 1),
            "intervention": "ncl",        # non-formal learning centre; see interventions
        })
        idx = np.array([i for i in idx if not used[i]])
        if idx.size == 0:
            break

    return picked


# ---------------------------------------------------------------------------- build

def build(code: str, facility_type: str) -> Path:
    boundary = load_district(code)
    name = str(boundary.iloc[0]["adm2_name"])
    province = str(boundary.iloc[0]["adm1_name"])
    utm = boundary.estimate_utm_crs()
    print(f"\n{code}  {name}, {province}   (UTM {utm.to_epsg()})")

    supply = load_schools(boundary, utm)
    demand = load_demand(boundary)
    print(f"  mapped schools {len(supply):,} | population hexes {len(demand):,}")
    if demand.empty:
        sys.exit("  no population hexes - nothing to build")

    demand = attach_rwi(demand, utm)
    dist_m, minutes = walk_minutes_to_supply(demand, supply, utm)

    pop = demand["population"].to_numpy(dtype=float)
    children = pop * config.CHILD_SHARE
    # Centroids are computed in the projected CRS and then converted, not taken in
    # degrees: a centroid of a lat/lon polygon is not the centroid of the real shape.
    centroids = gpd.GeoSeries(demand.to_crs(utm).centroid, crs=utm).to_crs(4326)
    lon = centroids.x.to_numpy()
    lat = centroids.y.to_numpy()
    cls = np.searchsorted(np.asarray(config.BANDS_MIN, dtype=float), minutes, side="left")

    underserved = minutes > config.THRESHOLD_MIN
    official = OFFICIAL_SCHOOLS.get(code)

    # No polygons are shipped at all. The browser rebuilds every hexagon from the
    # centroid it already has plus one shared template.
    #
    # The previous approach dissolved the hexes and simplified the result with a
    # tolerance that scaled with district area. In a 19,683 km2 district that worked out
    # at 444 m - roughly the edge length of a single hexagon - so instead of smoothing an
    # outline it shredded every cell into slivers. Sending real geometry instead costs
    # about 1 MB for that district.
    #
    # Measured over 4,000 Tharparkar hexes, one shared set of vertex offsets reproduces
    # every hexagon to within 3.9 m, which is 0.5% of an edge. So twelve numbers replace
    # a megabyte of coordinates, and the shapes are exact hexagons again.
    hex_offsets = _hex_template(demand, utm)

    bundle = {
        "code": code,
        "name": name,
        "province": province,
        "facility_type": facility_type,
        "params": {
            "kmh": config.WALK_KMH,
            "detour": config.DETOUR_FACTOR,
            "threshold_min": config.THRESHOLD_MIN,
            "bands_min": list(config.BANDS_MIN),
            "radius_m": round(config.walk_radius_m(), 1),
            "child_share": config.CHILD_SHARE,
            "no_supply_min": NO_SUPPLY_MIN,
        },
        "confidence": {
            "mapped": int(len(supply)),
            "official": official,
            "ratio": round(len(supply) / official, 3) if official else None,
            "province_mapped": PROVINCE_MAPPED.get(province),
            "province_official": OFFICIAL_PROVINCE.get(province),
            "province_ratio": (
                round(PROVINCE_MAPPED[province] / OFFICIAL_PROVINCE[province], 3)
                if province in OFFICIAL_PROVINCE and province in PROVINCE_MAPPED
                else None
            ),
            "verdict": "low",
            "note": (
                "Distances are to the nearest MAPPED school, and open school data for "
                "Pakistan is substantially incomplete. Three sources were tested for "
                "Sindh: OpenStreetMap 1,571, UNICEF Giga 1,557 (OSM-derived and so adding "
                "almost nothing), and Overture 6,042 - the last drawn mainly from Meta and "
                "therefore genuinely independent. Giga and Overture are merged here, "
                "de-duplicated at 75 m, giving 6,390 against roughly 48,000 government "
                "schools on the official count: about 13%. Everything here is therefore a "
                "list of places to FIELD-VERIFY, never a confirmed gap."
            ),
        },
        "totals": {
            "population": int(pop.sum()),
            "children_est": int(children.sum()),
            "children_underserved_est": int(children[underserved].sum()),
            "hexes": int(len(demand)),
        },
        "bounds": [round(float(v), 5) for v in demand.total_bounds],
        "cells": {
            "lon": [round(float(v), 5) for v in lon],
            "lat": [round(float(v), 5) for v in lat],
            "min": [round(float(m), 1) for m in minutes],
            "pop": [int(round(v)) for v in pop],
            "ch": [int(round(v)) for v in children],
            "rwi": [None if pd.isna(v) else round(float(v), 3) for v in demand["rwi"]],
            "cls": [int(c) for c in cls],
        },
        "hex_offsets_m": hex_offsets,
        "schools": json.loads(supply.to_crs(4326).to_json(drop_id=True)),
        "shortlist": shortlist(lon, lat, children, minutes),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{code}.json"
    text = json.dumps(bundle, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")

    pct = 100 * children[underserved].sum() / children.sum() if children.sum() else 0
    print(f"  children est {children.sum():,.0f} | beyond {config.THRESHOLD_MIN} min: "
          f"{children[underserved].sum():,.0f} ({pct:.0f}%)")
    print(f"  shortlist {len(bundle['shortlist'])} sites | wrote {path.name} "
          f"({len(text)/1000:.0f} KB)")

    if len(text) > MAX_BYTES:
        print(f"  WARNING: {len(text):,} bytes exceeds the {MAX_BYTES:,} budget")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--district", action="append", help="ADM2 pcode, repeatable")
    parser.add_argument("--province", help="build every district in this province")
    parser.add_argument("--facility-type", default=config.FACILITY_TYPE,
                        choices=["education", "health"])
    parser.add_argument("--list", action="store_true", help="list districts and exit")
    args = parser.parse_args()

    if args.list:
        admin = gpd.read_file(ADMIN2)
        for _, r in admin.sort_values(["adm1_name", "adm2_name"]).iterrows():
            print(f"  {r['adm2_pcode']:8} {r['adm2_name']:28} {r['adm1_name']}")
        return

    codes = list(args.district or [])
    if args.province:
        admin = gpd.read_file(ADMIN2)
        sel = admin[admin["adm1_name"].str.contains(args.province, case=False, na=False)]
        codes += sel["adm2_pcode"].tolist()
    if not codes:
        sys.exit("pass --district <PCODE> or --province <name>")

    for code in dict.fromkeys(codes):
        build(code, args.facility_type)

    write_index()


def write_index() -> None:
    """
    A small catalogue of the districts that have been built.

    The page reads this to populate its district picker, so a district that was never
    built simply does not appear - which is how the app degrades honestly instead of
    offering a district and then failing to load it.
    """
    entries = []
    for path in sorted(OUT.glob("PK*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        entries.append({
            "code": bundle["code"],
            "name": bundle["name"],
            "province": bundle["province"],
            "mapped": bundle["confidence"]["mapped"],
            "children_est": bundle["totals"]["children_est"],
            "underserved_est": bundle["totals"]["children_underserved_est"],
            "bytes": path.stat().st_size,
        })
    # A build stamp the page appends to every bundle request. Without it the browser
    # happily serves a bundle from a previous build forever, and a rebuild is invisible
    # to anyone who has opened the page before - which is exactly the person you least
    # want seeing stale numbers.
    index = {
        "built": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "districts": entries,
        "note": (
            "Districts built for this demo. The pipeline runs on any of Pakistan's 160 "
            "ADM2 districts; these are the ones bundled here."
        ),
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"\nindex.json: {len(entries)} districts")


if __name__ == "__main__":
    main()
