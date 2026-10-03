"""
GeoMind AI — web API.

The web page (web/index.html) calls these routes:
  GET  /health     wake-up check
  GET  /featured   the prebuilt featured districts
  POST /area       load an area: a featured district, a search result, or "around this point"
  POST /ask        answer a question about the loaded area

Run locally:   python app.py            (then open http://127.0.0.1:7860/docs)
On Render it is started with: uvicorn app:app --host 0.0.0.0 --port $PORT  (see render.yaml)
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from shapely.geometry import mapping


# Local development: read GROQ_API_KEY (and friends) from a .env file if one exists.
# On Hugging Face the key comes from the Space's secrets instead.
for _env in (os.path.join(os.path.dirname(__file__), ".env"), os.path.join(os.path.dirname(__file__), "..", ".env")):
    if os.path.exists(_env):
        for _line in open(_env, encoding="utf-8"):
            if "=" in _line and not _line.lstrip().startswith("#"):
                _k, _v = _line.strip().split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

from rasta import ai, analysis, data, draw, suggest  # noqa: E402  (after .env is loaded)

# Only our own web pages may call this API from a browser.
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://arahmanmdmajid-rasta-school-access.static.hf.space,"
    "http://127.0.0.1:8770,http://localhost:8770",
).split(",")

app = FastAPI(title="GeoMind AI API", version="1.0",
              description="Plain-English questions about school access to healthcare, answered with GeoPandas.")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

# ---------------------------------------------------------------------------
# A simple per-visitor limit so the public API can't use up the free AI quota
# ---------------------------------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request, bucket: str, per_minute: int) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    q, now = _hits[f"{bucket}:{ip}"], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(429, "Too many requests — wait a minute and try again.")
    q.append(now)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
class AreaRequest(BaseModel):
    featured: str | None = None          # e.g. "gulberg"
    photon: dict | None = None           # a search result: {"properties": {...}, "lonlat": [lon, lat]}
    lonlat: list[float] | None = None    # load the neighbourhood around this point


class AskRequest(BaseModel):
    question: str
    area_id: str | None = None
    pin: list[float] | None = None       # [lon, lat] of the user's map pin
    me: list[float] | None = None        # [lon, lat] from the browser, when the question is about "me"
    me_error: str | None = None          # set when the browser could not get a location


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def geojson(gdf) -> dict:
    return json.loads(gdf.to_json(drop_id=True))


def area_payload(area: data.Area) -> dict:
    ns, nf = len(area.schools), len(area.facilities)
    sparse = ns == 0 or nf == 0
    return {
        "area_id": area.id, "place": area.name, "source": area.source,
        "counts": {"schools": ns, "facilities": nf, "hospitals": len(analysis.features(area, "hospitals"))},
        "boundary": mapping(area.polygon), "schools": geojson(area.schools), "facilities": geojson(area.facilities),
        "sparse": sparse,
        "suggestions": [] if sparse else suggest.build(area),
        "message": (f"I found very little mapped data for {area.name} ({ns} schools, {nf} medical facilities), so most "
                    "analyses won't work. Try a featured district or a larger area.") if sparse else
                   (f"Now showing {area.name}: {ns} schools and {nf} medical facilities. "
                    "Tip: click the map to drop a pin for “near this location” questions."),
    }


def describe(intent: dict) -> str:
    parts = [f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
             for k, v in intent["params"].items() if k != "place" and v not in (None, "")]
    return f"{intent['operation']}({', '.join(parts)})"


def featured_names() -> dict[str, str]:
    return {k: v["name"] for k, v in data.featured_catalog().items()}


GENERIC_PLACE = re.compile(r"^(this|here|the|my|our)\b|neighbo|area|district|location|city", re.I)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """<html><head><meta charset="utf-8"><title>GeoMind AI API</title></head>
    <body style="font-family:system-ui;max-width:640px;margin:40px auto;line-height:1.6">
    <h1>GeoMind AI API</h1>
    <p>The analysis server behind GeoMind AI. It is called by the web app; interactive docs are at
    <a href="/docs">/docs</a>.</p>
    <ul><li><code>GET /health</code></li><li><code>GET /featured</code></li>
    <li><code>POST /area</code></li><li><code>POST /ask</code></li></ul></body></html>"""


@app.get("/health")
def health():
    return {"ok": True, "ai": bool(os.environ.get("GROQ_API_KEY")), "model": ai.MODEL}


@app.get("/featured")
def featured():
    return [{"key": k, "name": v} for k, v in featured_names().items()]


@app.post("/area")
def load_area(body: AreaRequest, request: Request):
    rate_limit(request, "area", 30)
    try:
        if body.featured:
            if body.featured not in data.featured_catalog():
                raise HTTPException(404, f"Unknown featured district: {body.featured}")
            area = data.load_featured(body.featured)
        elif body.photon:
            props, lonlat = body.photon.get("properties", {}), body.photon.get("lonlat") or [None, None]
            key = data.match_featured(props)
            area = data.load_featured(key) if key else data.load_live(props, lonlat[0], lonlat[1])
        elif body.lonlat:
            area = data.load_around_point(body.lonlat[0], body.lonlat[1])
        else:
            raise HTTPException(400, "Send one of: featured, photon, lonlat.")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[area] load failed: {exc}")
        raise HTTPException(502, "Couldn't load map data for that area — the public map servers may be busy. "
                                 "Try again or pick a featured district.")
    return area_payload(area)


@app.post("/ask")
def ask(body: AskRequest, request: Request):
    rate_limit(request, "ask", 20)
    started = time.time()
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Question is empty.")

    area, moved = data.get_area(body.area_id), False
    pin = body.pin

    # No area loaded yet: a featured name in the question, or "near me", can still work.
    if area is None:
        key = data.featured_in_text(q)
        if key:
            area, moved = data.load_featured(key), True
        elif ai.ME_RE.search(q.lower()):
            if not body.me:
                if body.me_error:
                    return {"answer": "I need your location permission to answer that. Allow location access, "
                                      "or search for an area above.", "needs_area": True}
                return {"needs_location": True}
            try:
                area, moved = data.load_around_point(*body.me), True
            except Exception:
                return {"answer": "I couldn't load map data around your location. Search for an area above instead.",
                        "needs_area": True}
        else:
            return {"answer": "Choose a location first — search above or tap a featured district, or ask about "
                              "schools near you.", "needs_area": True}

    intent, router = ai.route(q, area.name, featured_names())
    p = intent["params"]
    notes: list[str] = []
    me_inside = None

    # "Near me": use the person's real position, moving to their neighbourhood if needed.
    if intent["operation"] == "find" and p.get("from") == "me":
        fallback = "your map pin" if pin else "the centre of the area"
        if body.me is None and body.me_error is None:
            return {"needs_location": True}
        if body.me:
            if area.contains(*body.me):
                me_inside = body.me
            else:
                away = analysis.fmt(analysis.to_metres(*body.me, area).distance(area.boundary_m.geometry.iloc[0].centroid))
                try:
                    new_area = data.load_around_point(*body.me)
                    area, moved, pin = new_area, True, None
                    me_inside = body.me if area.contains(*body.me) else None
                    notes_prefix = f"You're about {away} from your previous area, so I moved the map to {area.name}."
                    notes.append(notes_prefix)
                except Exception:
                    notes.append(f"Your location is about {away} from {analysis.short_name(area)} and I couldn't load "
                                 f"data around you, so I used {fallback} instead.")
        else:
            notes.append(f"I couldn't get your location (permission denied or unavailable), so I used {fallback} instead.")

    # A named place in the question: switch to it if it's a featured district.
    place = p.get("place")
    if place and not GENERIC_PLACE.search(place) and place.split(",")[0].strip().lower() not in area.name.lower():
        key = data.featured_in_text(place)
        if key:
            if data.featured_catalog()[key]["name"] != area.name:
                area, moved, pin = data.load_featured(key), True, None
        else:
            return {"answer": f"I don't have {place} loaded. Search for it above, then ask again.", "needs_area": True,
                    "tag": describe(intent), "router": router}

    if intent["operation"] == "unsupported":
        return {"answer": f"I can answer questions about schools and healthcare in {area.name}: counts, distances, "
                          "buffers, coverage gaps, nearby places and area summaries.",
                "tag": describe(intent), "router": router, "area": area_payload(area) if moved else None}

    result = analysis.run(area, intent["operation"], p, pin=pin, me=me_inside, notes=notes)
    answer, explainer = ai.explain(q, result["text"], area.name, area.source)
    return {
        "answer": answer,
        "facts": result["text"],
        "tag": describe(intent),
        "router": router,              # "ai" or "keywords"
        "explainer": explainer,        # "ai" or "computed"
        "draw": draw.layers(result),
        "followups": suggest.follow_ups(area, result),
        "area": area_payload(area) if moved else None,
        "timing_ms": round((time.time() - started) * 1000),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
