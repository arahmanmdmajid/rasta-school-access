"""
Fetch Pakistan school locations from Overture Maps.

This is the source that rescued the project. Measured on 2026-10-03 for Sindh:

    OpenStreetMap   1,571      Tharparkar: 2
    UNICEF Giga     1,557      Tharparkar: 2      (OSM-derived; adds nothing here)
    Overture        6,042      Tharparkar: 228

Overture's upstream breakdown for those 6,042 is meta=6,015, Microsoft=23, Foursquare=4,
so it is genuinely independent of OpenStreetMap rather than a re-serving of it. That is
what makes a rural district like Tharparkar analysable at all.

Reads Overture's public S3 parquet with DuckDB - no bulk download, no CLI.
Build time only. Licence: CDLA-Permissive 2.0, which is redistributable.

    python scripts/fetch_overture.py
    python scripts/fetch_overture.py --refresh
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from rasta import config  # noqa: E402

RAW = ROOT / "data" / "raw"
CATALOG = "https://stac.overturemaps.org/catalog.json"
S3 = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"

# Generous bounding box over Pakistan; the admin join later decides what is really inside.
PAKISTAN = (60.5, 23.5, 77.5, 37.2)


def category_filter(words: tuple[str, ...]) -> str:
    """
    Overture renamed `categories` to `taxonomy` in recent releases and added
    `basic_category`. Both are matched so this keeps working across a release bump.
    """
    clauses = []
    for w in words:
        clauses.append(f"taxonomy.primary ILIKE '%{w}%'")
        clauses.append(f"basic_category ILIKE '%{w}%'")
    return " OR ".join(clauses)


def latest_release() -> str:
    with urllib.request.urlopen(CATALOG, timeout=60) as fh:
        catalog = json.load(fh)
    releases = [
        link["href"].rsplit("/", 2)[-2]
        for link in catalog.get("links", [])
        if link.get("rel") == "child"
    ]
    if not releases:
        sys.exit("could not read a release from the Overture STAC catalog")
    return sorted(releases)[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facility-type", default=config.FACILITY_TYPE,
                        choices=sorted(config.FACILITY_TYPES))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    spec = config.facility(args.facility_type)
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"overture_pak_{args.facility_type}.json"
    if out.exists() and not args.refresh:
        rows = json.loads(out.read_text(encoding="utf-8"))
        print(f"cached: {len(rows):,} {spec['plural']} in {out}  (use --refresh to re-fetch)")
        return

    release = latest_release()
    xmin, ymin, xmax, ymax = PAKISTAN
    print(f"Overture release {release} — scanning {spec['plural']} over Pakistan (a few minutes)…")

    con = duckdb.connect()
    # spatial is needed for ST_X/ST_Y over Overture's GEOMETRY column; httpfs for S3.
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; "
                "SET s3_region='us-west-2';")
    rows = con.execute(f"""
        SELECT
            names.primary                        AS name,
            taxonomy.primary                     AS category,
            ST_X(ST_Centroid(geometry))          AS longitude,
            ST_Y(ST_Centroid(geometry))          AS latitude,
            confidence
        FROM read_parquet('{S3.format(release=release)}', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {xmin} AND {xmax}
          AND bbox.ymin BETWEEN {ymin} AND {ymax}
          AND ({category_filter(spec["overture"])})
    """).fetchall()

    records = [
        {"name": n or "", "category": c or "", "longitude": lon, "latitude": lat,
         "confidence": conf, "source": "overture"}
        for n, c, lon, lat, conf in rows
        if lon is not None and lat is not None
    ]
    out.write_text(json.dumps(records), encoding="utf-8")

    print(f"wrote {len(records):,} {spec['plural']} to {out}")
    named = sum(1 for r in records if r["name"])
    print(f"  named            {named:,}")
    thar = [r for r in records if 69.0 <= r["longitude"] <= 71.1 and 24.2 <= r["latitude"] <= 25.6]
    sindh = [r for r in records if 66.5 <= r["longitude"] <= 71.2 and 23.5 <= r["latitude"] <= 28.6]
    print(f"  rough Sindh      {len(sindh):,}   (OpenStreetMap 1,571 | Giga 1,557)")
    print(f"  rough Tharparkar {len(thar):,}   (OpenStreetMap 2 | Giga 2)")


if __name__ == "__main__":
    main()
