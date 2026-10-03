"""
Spike: how many schools does Overture Maps have in Sindh, and in Tharparkar?

This is the last branch of the M1 school-data ladder. OpenStreetMap has 1,571 schools
for all of Sindh and 2 in Tharparkar; UNICEF Giga turned out to be OSM-derived here
(4,701 for all of Pakistan, 1,557 in Sindh, 2 in Tharparkar). Overture aggregates Meta
and Microsoft place data as well as OSM, so it is the only remaining chance of better
rural coverage.

Reads Overture's public S3 parquet directly with DuckDB - no CLI, no bulk download.
Build time only.

    python scripts/probe_overture.py

Data: Overture Maps Foundation, places theme, CDLA-Permissive 2.0.
"""

from __future__ import annotations

import json
import sys
import urllib.request

import duckdb

CATALOG = "https://stac.overturemaps.org/catalog.json"
S3 = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"

# Bounding boxes used for every source so the comparison is like for like.
BOXES = {
    "Sindh (rough)": (66.5, 23.5, 71.2, 28.6),
    "Tharparkar (rough)": (69.0, 24.2, 71.1, 25.6),
    "Karachi (rough)": (66.6, 24.7, 67.5, 25.15),
}

BASELINE = {
    "Sindh (rough)": "OSM 1,571 / Giga 1,557",
    "Tharparkar (rough)": "OSM 2 / Giga 2",
    "Karachi (rough)": "OSM ~1,266",
}


def latest_release() -> str:
    with urllib.request.urlopen(CATALOG, timeout=60) as fh:
        catalog = json.load(fh)
    releases = [
        link["href"].rsplit("/", 2)[-2]
        for link in catalog.get("links", [])
        if link.get("rel") == "child"
    ]
    if not releases:
        sys.exit("could not read any release from the Overture STAC catalog")
    return sorted(releases)[-1]


def main() -> None:
    release = latest_release()
    source = S3.format(release=release)
    print(f"Overture release: {release}\n")

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")

    for label, (xmin, ymin, xmax, ymax) in BOXES.items():
        # Overture renamed `categories` to `taxonomy` in recent releases, and added
        # `basic_category` plus a `sources` array that records which upstream dataset
        # each place came from - which is exactly the provenance Giga did not give us.
        where = f"""
            WHERE bbox.xmin BETWEEN {xmin} AND {xmax}
              AND bbox.ymin BETWEEN {ymin} AND {ymax}
              AND (
                    taxonomy.primary ILIKE '%school%'
                 OR taxonomy.primary ILIKE '%education%'
                 OR taxonomy.primary ILIKE '%kindergarten%'
                 OR basic_category ILIKE '%school%'
              )
        """
        try:
            n = con.execute(
                f"SELECT count(*) FROM read_parquet('{source}', hive_partitioning=1) {where}"
            ).fetchone()[0]
            print(f"  {label:22} {n:>7,}   (baseline: {BASELINE[label]})")

            if n:
                rows = con.execute(f"""
                    SELECT s.dataset, count(*) AS n
                    FROM read_parquet('{source}', hive_partitioning=1),
                         UNNEST(sources) AS t(s)
                    {where}
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 5
                """).fetchall()
                breakdown = ", ".join(f"{d}={c:,}" for d, c in rows)
                print(f"  {'':22} upstream: {breakdown}")
        except Exception as exc:
            print(f"  {label:22} FAILED  {str(exc)[:200]}")
            break


if __name__ == "__main__":
    main()
