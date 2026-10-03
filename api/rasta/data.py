"""
Loading map data for an AREA.

An area is one district or neighbourhood with three layers:
  - its boundary polygon
  - the schools inside it
  - the medical facilities inside it (hospitals, clinics, doctors, dentists)

Where the data comes from:
  1. Featured districts (Ghirnatah, Gulberg, Clifton): prebuilt Overture Maps extracts in
     data/featured.json. Fast, and names are well filled in.
  2. Anywhere else: live OpenStreetMap data from the public Overpass API.

Boundaries and "which neighbourhood am I in?" come from Nominatim (OpenStreetMap's geocoder).
Everything is stored as GeoPandas GeoDataFrames in EPSG:4326 (longitude/latitude), with
metre-based copies in the area's UTM zone for measuring distances.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import Point, box, shape

# Nominatim's usage policy asks every app to identify itself.
USER_AGENT = "GeoMindAI/1.0 (hackathon project; https://github.com)"

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_TIMEOUT_S = 25          # busy public mirrors often need ~20 s

# How we recognise a featured district from a search result or a question.
FEATURED_MATCH = {
    "ghirnatah": {"cc": "sa", "aliases": ["ghirnatah", "غرناطة"]},
    "gulberg":   {"cc": "pk", "aliases": ["gulberg", "گلبرگ"]},
    "clifton":   {"cc": "pk", "aliases": ["clifton", "کلفٹن"]},
}


# ---------------------------------------------------------------------------
# The Area object
# ---------------------------------------------------------------------------
@dataclass
class Area:
    id: str
    name: str
    source: str                      # "Overture extract" or "Live OpenStreetMap"
    boundary: gpd.GeoDataFrame       # one polygon row, EPSG:4326
    schools: gpd.GeoDataFrame        # points with columns name, category
    facilities: gpd.GeoDataFrame

    @cached_property
    def utm(self):
        """The UTM zone for this place, so distances come out in metres."""
        return self.boundary.estimate_utm_crs()

    @cached_property
    def boundary_m(self) -> gpd.GeoDataFrame:
        return self.boundary.to_crs(self.utm)

    @cached_property
    def schools_m(self) -> gpd.GeoDataFrame:
        return self.schools.to_crs(self.utm)

    @cached_property
    def facilities_m(self) -> gpd.GeoDataFrame:
        return self.facilities.to_crs(self.utm)

    @property
    def polygon(self):
        return self.boundary.geometry.iloc[0]

    def contains(self, lon: float, lat: float) -> bool:
        return bool(self.polygon.covers(Point(lon, lat)))

    def centre(self) -> tuple[float, float]:
        c = self.boundary_m.geometry.iloc[0].centroid
        ll = gpd.GeoSeries([c], crs=self.utm).to_crs(4326).iloc[0]
        return (ll.x, ll.y)


def _points(features: list[dict]) -> gpd.GeoDataFrame:
    """GeoJSON point features -> GeoDataFrame with name and category columns."""
    if not features:
        return gpd.GeoDataFrame({"name": [], "category": []}, geometry=[], crs=4326)
    gdf = gpd.GeoDataFrame.from_features(features, crs=4326)
    for col in ("name", "category"):
        if col not in gdf:
            gdf[col] = ""
        gdf[col] = gdf[col].fillna("").astype(str)
    return gdf[["name", "category", "geometry"]].reset_index(drop=True)


def _boundary(geom) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[geom], crs=4326)


# ---------------------------------------------------------------------------
# A small in-memory cache so the page can refer to an area by its id
# ---------------------------------------------------------------------------
_CACHE: "OrderedDict[str, Area]" = OrderedDict()
_CACHE_SIZE = 32


def remember(area: Area) -> Area:
    _CACHE[area.id] = area
    _CACHE.move_to_end(area.id)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return area


def get_area(area_id: str | None) -> Area | None:
    if not area_id:
        return None
    if area_id in _CACHE:
        return _CACHE[area_id]
    if area_id.startswith("featured:"):          # featured areas can always be rebuilt
        key = area_id.split(":", 1)[1]
        if key in featured_catalog():
            return load_featured(key)
    return None


# ---------------------------------------------------------------------------
# Featured districts (prebuilt Overture Maps extracts)
# ---------------------------------------------------------------------------
def _featured_path() -> Path:
    here = Path(__file__).resolve().parent
    for p in (here.parent / "data" / "featured.json",          # deployed: api root/data
              here.parent.parent / "data" / "featured.json"):  # repo: repo root/data
        if p.exists():
            return p
    raise FileNotFoundError("data/featured.json not found")


@lru_cache(maxsize=1)
def featured_catalog() -> dict:
    return json.loads(_featured_path().read_text(encoding="utf-8"))


def load_featured(key: str) -> Area:
    d = featured_catalog()[key]
    area = Area(
        id=f"featured:{key}",
        name=d["name"],
        source="Overture extract",
        boundary=_boundary(shape(d["boundary"]["geometry"])),
        schools=_points(d["schools"]["features"]),
        facilities=_points(d["facilities"]["features"]),
    )
    return remember(area)


def featured_in_text(text: str) -> str | None:
    """Which featured district (if any) is named in a piece of text?"""
    t = (text or "").lower()
    for key, m in FEATURED_MATCH.items():
        if key in featured_catalog() and any(a in t for a in m["aliases"]):
            return key
    return None


def match_featured(props: dict) -> str | None:
    """Does a search result (Photon/Nominatim properties) point at a featured district?"""
    ref = (props.get("osm_type") or "")[:1].upper() + str(props.get("osm_id") or "")
    for key, d in featured_catalog().items():
        if d.get("osm") == ref:
            return key
    hay = " ".join(str(props.get(k) or "") for k in ("name", "city", "state")).lower()
    cc = str(props.get("countrycode") or props.get("country_code") or "").lower()
    for key, m in FEATURED_MATCH.items():
        if key in featured_catalog() and cc == m["cc"] and any(a in hay for a in m["aliases"]):
            return key
    return None


# ---------------------------------------------------------------------------
# Live OpenStreetMap areas
# ---------------------------------------------------------------------------
def _get_json(url: str, params: dict, timeout: float = 10):
    params = {"accept-language": "en", **params}          # English place names where available
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _overpass(query: str) -> list[dict]:
    """Ask all public Overpass mirrors at once and use the first good answer."""
    def post(url):
        r = requests.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT},
                          timeout=OVERPASS_TIMEOUT_S)
        r.raise_for_status()
        return r.json().get("elements", [])

    pool = ThreadPoolExecutor(max_workers=len(OVERPASS_MIRRORS))
    futures = {pool.submit(post, url): url for url in OVERPASS_MIRRORS}
    errors = []
    try:
        for fut in as_completed(futures):
            try:
                return fut.result()
            except Exception as exc:              # this mirror failed; wait for the others
                errors.append(f"{futures[fut]}: {exc}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)   # don't wait for slower mirrors
    raise RuntimeError("All Overpass servers failed: " + "; ".join(errors))


def _dedupe(gdf: gpd.GeoDataFrame, utm, metres: float = 75) -> gpd.GeoDataFrame:
    """Drop facilities within `metres` of one already kept (the same place mapped twice)."""
    if gdf.empty:
        return gdf
    projected = gdf.to_crs(utm)
    kept = []
    for idx, geom in projected.geometry.items():
        if all(geom.distance(projected.geometry[k]) > metres for k in kept):
            kept.append(idx)
    return gdf.loc[kept].reset_index(drop=True)


def load_live(props: dict, lon: float, lat: float, boundary_geojson: dict | None = None) -> Area:
    """
    Load an area that is not featured, from live OpenStreetMap data.

    props: the search result's properties (name, city, country, osm_type, osm_id, extent).
    lon, lat: the search result's point, used if there is no extent.
    """
    name = ", ".join(str(props[k]) for k in ("name", "city", "country") if props.get(k))
    geom = shape(boundary_geojson) if boundary_geojson else None

    # 1. A real boundary polygon, if OpenStreetMap has one for this place.
    if geom is None and props.get("osm_type") and props.get("osm_id"):
        ref = str(props["osm_type"])[:1].upper() + str(props["osm_id"])
        try:
            found = _get_json("https://nominatim.openstreetmap.org/lookup",
                              {"osm_ids": ref, "format": "jsonv2", "polygon_geojson": 1}, timeout=8)
            if found and "Polygon" in found[0].get("geojson", {}).get("type", ""):
                geom = shape(found[0]["geojson"])
        except Exception:
            pass                                   # fall back to a bounding box below

    # 2. The bounding box we query. Photon's "extent" is [west, north, east, south].
    if props.get("extent"):
        w, n, e, s = props["extent"]
    else:
        w, s, e, n = lon - 0.02, lat - 0.02, lon + 0.02, lat + 0.02
    if geom is None:
        geom = box(w, s, e, n)

    # 3. Schools and medical facilities from Overpass.
    query = (f"[out:json][timeout:45];("
             f'nwr["amenity"~"^(school|kindergarten)$"]({s},{w},{n},{e});'
             f'nwr["amenity"~"^(hospital|clinic|doctors|dentist)$"]({s},{w},{n},{e});'
             f'nwr["healthcare"]({s},{w},{n},{e}););out center tags;')
    elements = _overpass(query)

    schools, medical = [], []
    for el in elements:
        tags = el.get("tags", {})
        c = el.get("center") or {"lon": el.get("lon"), "lat": el.get("lat")}
        if c.get("lon") is None or c.get("lat") is None:
            continue
        feature = {"type": "Feature",
                   "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                   "properties": {"name": tags.get("name") or tags.get("name:en") or ""}}
        if tags.get("amenity") in ("school", "kindergarten"):
            feature["properties"]["category"] = tags["amenity"]
            schools.append(feature)
        elif tags.get("amenity") in ("hospital", "clinic", "doctors", "dentist") or tags.get("healthcare"):
            feature["properties"]["category"] = tags.get("amenity") or tags.get("healthcare")
            medical.append(feature)

    boundary = _boundary(geom)
    school_gdf, fac_gdf = _points(schools), _points(medical)
    # Keep only places inside the area's real boundary, like the featured extracts.
    school_gdf = school_gdf[school_gdf.within(geom) | school_gdf.touches(geom)].reset_index(drop=True)
    fac_gdf = fac_gdf[fac_gdf.within(geom) | fac_gdf.touches(geom)].reset_index(drop=True)

    area_id = "live:" + hashlib.sha1(f"{name}|{geom.bounds}".encode()).hexdigest()[:12]
    area = Area(id=area_id, name=name or "Selected area", source="Live OpenStreetMap",
                boundary=boundary, schools=school_gdf, facilities=fac_gdf)
    area.facilities = _dedupe(area.facilities, area.utm)
    area.__dict__.pop("facilities_m", None)       # reset the cached projected copy
    return remember(area)


def load_around_point(lon: float, lat: float) -> Area:
    """Find the neighbourhood a person is standing in and load it."""
    props, boundary_geojson = None, None
    try:
        d = _get_json("https://nominatim.openstreetmap.org/reverse",
                      {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14,
                       "polygon_geojson": 1, "addressdetails": 1}, timeout=8)
        if d and d.get("boundingbox"):
            s, n, w, e = map(float, d["boundingbox"])
            bbox_km2 = gpd.GeoSeries([box(w, s, e, n)], crs=4326).to_crs(
                gpd.GeoSeries([box(w, s, e, n)], crs=4326).estimate_utm_crs()).area.iloc[0] / 1e6
            if bbox_km2 <= 60:                     # bigger than a district would be too slow live
                a = d.get("address", {})
                props = {"name": a.get("suburb") or a.get("neighbourhood") or a.get("quarter")
                                 or a.get("city_district") or d.get("name") or "Your area",
                         "city": a.get("city") or a.get("town") or a.get("village") or a.get("county"),
                         "country": a.get("country"), "countrycode": a.get("country_code"),
                         "osm_type": d.get("osm_type"), "osm_id": d.get("osm_id"),
                         "extent": [w, n, e, s]}
                gj = d.get("geojson") or {}
                if "Polygon" in gj.get("type", "") and shape(gj).covers(Point(lon, lat)):
                    boundary_geojson = gj
    except Exception:
        pass

    if props is None:                              # no neighbourhood found: 1.5 km around the person
        centre = gpd.GeoSeries([Point(lon, lat)], crs=4326)
        utm = centre.estimate_utm_crs()
        w, s, e, n = centre.to_crs(utm).buffer(1500).to_crs(4326).total_bounds
        props = {"name": "Area around your location", "extent": [w, n, e, s]}

    key = match_featured(props)
    if key:
        return load_featured(key)
    return load_live(props, lon, lat, boundary_geojson)
