"""
Download the open datasets Rasta is built from, into gitignored data/raw/.

Build time only. Nothing here is needed by the deployed service - the district bundles
produced from these files are what ship.

    python scripts/fetch_data.py            # everything missing

Sources and licences are listed in rasta/config.py SOURCES and in the README.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

HDX = "https://data.humdata.org/dataset"
COD = f"{HDX}/a64d1ff2-7158-48c7-887d-6af69ce21906/resource"
RWI = f"{HDX}/76f2a2ea-ba50-40f5-b79c-db95d668b843/resource"

FILES = {
    # Read this one FIRST: it names the admin columns, and at 222 KB it is far cheaper
    # than opening the 25 MB shapefile to discover that District == ADM2.
    "admin_xlsx": (
        f"{COD}/cd81f0e7-50c7-4861-9e65-1f3134a7f5a5/download/pak_admin_boundaries.xlsx",
        "pak_admin_boundaries.xlsx",
    ),
    "admin_shp": (
        f"{COD}/d2752403-3c34-4e03-8b4e-3e55500ded10/download/pak_admin_boundaries.shp.zip",
        "pak_admin_boundaries.shp.zip",
    ),
    "population": (
        "https://geodata-eu-central-1-kontur-public.s3.amazonaws.com/kontur_datasets/"
        "kontur_population_PK_20231101.gpkg.gz",
        "kontur_population_PK_20231101.gpkg.gz",
    ),
    "rwi": (
        f"{RWI}/977923ab-c65a-4203-b216-e4b7483d56a5/download/ind_pak_relative_wealth_index.csv",
        "ind_pak_relative_wealth_index.csv",
    ),
}


def download(key: str, url: str, name: str) -> None:
    dest = RAW / name
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  {key:12} cached  {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return

    print(f"  {key:12} downloading {url.rsplit('/', 1)[-1]} ...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    print(f"  {key:12} done    {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")

    # Kontur ships gzipped; unpack so pyogrio can read the .gpkg directly.
    if dest.suffix == ".gz":
        out = dest.with_suffix("")
        if not out.exists():
            print(f"  {key:12} gunzip  -> {out.name}")
            with gzip.open(dest, "rb") as fin, out.open("wb") as fout:
                shutil.copyfileobj(fin, fout)
            print(f"  {key:12} ready   {out.name} ({out.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    print(f"data/raw -> {RAW}")
    failed = []
    # Anything already downloaded is skipped, so re-fetching one file is deleting it.
    for key in FILES:
        url, name = FILES[key]
        try:
            download(key, url, name)
        except Exception as exc:                      # keep going; report at the end
            print(f"  {key:12} FAILED  {exc}")
            failed.append(key)

    if failed:
        sys.exit(f"\nfailed: {', '.join(failed)}")
    print("\nall datasets present")


if __name__ == "__main__":
    main()
