"""
Fetch every Pakistan school location from the UNICEF Giga API and cache it.

This is the M1 gate: it answers how many schools Giga actually holds for Pakistan and
how they are spread across districts, which decides whether the demo leads with rural
Sindh or with Karachi Division.

Run once; the result is cached to data/raw/giga_pak.json and never re-fetched unless
--refresh is passed. Build-time only - the API key never reaches the deployed service.

    python scripts/fetch_giga.py
    python scripts/fetch_giga.py --country PAK --refresh

Data: UNICEF Giga, ODbL. Credit: Giga and its contributors. Portions derived from
OpenStreetMap.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
API = "https://uni-ooi-giga-maps-service.azurewebsites.net/api/v1"
PAGE_SIZE = 1000          # the API requires `size`; we probe what it will actually give
TIMEOUT_S = 90


def load_env() -> None:
    """Read .env at the repo root, without adding a python-dotenv dependency."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_key() -> str:
    key = os.environ.get("GIGA_SCHOOL_LOCATION_API_KEY", "").strip()
    if not key:
        sys.exit(
            "GIGA_SCHOOL_LOCATION_API_KEY is not set.\n"
            "Put it in .env at the repo root (that file is gitignored):\n"
            "    GIGA_SCHOOL_LOCATION_API_KEY=your_key_here\n"
            "Get a key at https://maps.giga.global/"
        )
    return key


def fetch_country(country: str, key: str) -> list[dict]:
    """Page the country endpoint to exhaustion."""
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})

    rows: list[dict] = []
    seen: set[str] = set()
    page = 1

    while True:
        url = f"{API}/schools_location/country/{country}"
        resp = session.get(url, params={"size": PAGE_SIZE, "page": page}, timeout=TIMEOUT_S)

        if resp.status_code == 404:
            print(f"  page {page}: 404 - no more data")
            break
        if resp.status_code != 200:
            print(f"  page {page}: HTTP {resp.status_code} {resp.text[:200]}")
            break

        body = resp.json()
        batch = body.get("data") or []
        if not batch:
            print(f"  page {page}: empty - done")
            break

        # The API has no documented total, so de-duplicate on giga_id_school and stop
        # when a page adds nothing new (guards against a server that ignores `page`).
        added = 0
        for row in batch:
            gid = row.get("giga_id_school")
            if gid and gid in seen:
                continue
            if gid:
                seen.add(gid)
            rows.append(row)
            added += 1

        print(f"  page {page}: {len(batch)} returned, {added} new, {len(rows)} total")

        if added == 0:
            print("  page added nothing new - stopping")
            break
        if len(batch) < PAGE_SIZE:
            print("  short page - reached the end")
            break

        page += 1
        time.sleep(0.2)          # be polite to a UN service

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="PAK", help="ISO3 country code (default PAK)")
    parser.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"giga_{args.country.lower()}.json"

    if out.exists() and not args.refresh:
        rows = json.loads(out.read_text(encoding="utf-8"))
        print(f"cached: {len(rows):,} schools in {out}  (use --refresh to re-fetch)")
    else:
        load_env()
        print(f"fetching {args.country} from Giga...")
        rows = fetch_country(args.country, api_key())
        if not rows:
            sys.exit("no rows returned - nothing written")
        out.write_text(json.dumps(rows), encoding="utf-8")
        print(f"\nwrote {len(rows):,} schools to {out}")

    # --- what the gate actually needs to know ---
    with_coords = [
        r for r in rows
        if isinstance(r.get("latitude"), (int, float))
        and isinstance(r.get("longitude"), (int, float))
    ]
    levels: dict[str, int] = {}
    sources: dict[str, int] = {}
    for r in rows:
        levels[str(r.get("education_level"))] = levels.get(str(r.get("education_level")), 0) + 1
        sources[str(r.get("school_data_source"))] = sources.get(str(r.get("school_data_source")), 0) + 1

    print(f"\n  total records     {len(rows):,}")
    print(f"  with coordinates  {len(with_coords):,}")
    print(f"  education_level   {dict(sorted(levels.items(), key=lambda kv: -kv[1])[:6])}")
    print(f"  data_source       {dict(sorted(sources.items(), key=lambda kv: -kv[1])[:4])}")

    if with_coords:
        lats = [r["latitude"] for r in with_coords]
        lons = [r["longitude"] for r in with_coords]
        print(f"  bbox              lon {min(lons):.3f}..{max(lons):.3f}  "
              f"lat {min(lats):.3f}..{max(lats):.3f}")

        # Crude Sindh box, just to see at a glance whether the south is covered.
        sindh = [r for r in with_coords if 66.5 <= r["longitude"] <= 71.2 and 23.5 <= r["latitude"] <= 28.6]
        thar = [r for r in with_coords if 69.0 <= r["longitude"] <= 71.1 and 24.2 <= r["latitude"] <= 25.6]
        print(f"\n  rough Sindh bbox      {len(sindh):,}   (OpenStreetMap has 1,571 for all Sindh)")
        print(f"  rough Tharparkar bbox {len(thar):,}   (OpenStreetMap has 2)")


if __name__ == "__main__":
    main()
