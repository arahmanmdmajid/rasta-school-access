"""
The AI's two small jobs — and nothing else.

1. ROUTE: read the user's question and fill in ONE analysis block as JSON,
   e.g. {"operation": "find", "params": {"target": "schools", "relation": "beyond", "distance_m": 2000}}
2. EXPLAIN: turn the facts our GIS code computed into 1-3 friendly sentences,
   using only those facts (so it cannot invent numbers, names or sources).

If the AI is unavailable (no key, network error, free-tier rate limit), a keyword router and the
computed text are used instead, so the app always answers.
"""
from __future__ import annotations

import json
import os
import re

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
OPERATIONS = ("find", "coverage", "distance_grid", "summary", "unsupported")

# Questions about the user's OWN position. "Show me ..." deliberately does not match.
ME_RE = re.compile(r"\b(near|closest to|nearest to|around|from) me\b|\bmy (location|position|area|neighbou?rhood)\b|where i am")

_client = None


def _groq():
    """Create the Groq client once, only if a key is configured."""
    global _client
    if _client is None and os.environ.get("GROQ_API_KEY"):
        from groq import Groq
        _client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=30, max_retries=0)
    return _client


ROUTER_PROMPT = """You turn a question about schools and healthcare in __PLACE__ into ONE analysis block.
Return ONLY JSON: {"operation": "...", "params": {...}}.

Blocks:
- find: list or count features. params:
    target: "schools" | "facilities" | "hospitals" | "all"   ("facilities" = any medical facility, clinic, doctor, healthcare)
    relation: "all" | "within" | "beyond"
    from: "facilities" | "schools" | "hospitals" | "pin" | "me" | "centre"
          ("pin" = "this location"/"here"; "me" = "near me"/"my location"; "centre" = "this area"/"the area")
    distance_m: number in METRES (convert km to m) — needed for within/beyond
    sort: "nearest" | "farthest"
    output: "list" | "count"
    limit: number
- coverage: parts of the area farther than a distance from the target. params: target ("facilities" or "hospitals"), distance_m
- distance_grid: colour map of distance to the nearest target across the area. params: target
- summary: overview of the area (size, counts, densities, average distance, farthest school). params: {}
- unsupported: ONLY for topics unrelated to schools, healthcare, access, distances, buffers or maps.

Use from "me" ONLY when the user asks about their OWN position ("near me", "closest to me", "my location").
"Show me ..." does NOT mean from "me". "Closest to medical facilities" means from "facilities".
Use from "pin" for "this location", "here" or a radius with no other reference.
Add params.place ONLY when the question names a specific place (e.g. "Gulberg, Lahore").
Anything about schools, hospitals, clinics, healthcare access, buffers, distances or maps IS in scope.

Examples:
"How many schools are there?" -> {"operation":"find","params":{"target":"schools","relation":"all","output":"count"}}
"How many medical facilities are in Gulberg, Lahore?" -> {"operation":"find","params":{"target":"facilities","relation":"all","output":"count","place":"Gulberg, Lahore"}}
"Which schools are within 2 km of medical facilities?" -> {"operation":"find","params":{"target":"schools","relation":"within","from":"facilities","distance_m":2000}}
"Create a 2 km buffer around medical facilities" -> {"operation":"find","params":{"target":"schools","relation":"within","from":"facilities","distance_m":2000}}
"Which medical facilities are within 2 km of schools?" -> {"operation":"find","params":{"target":"facilities","relation":"within","from":"schools","distance_m":2000}}
"Which schools are closest to medical facilities?" -> {"operation":"find","params":{"target":"schools","relation":"all","from":"facilities","sort":"nearest","limit":5}}
"Which schools are more than 3 km from a medical facility?" -> {"operation":"find","params":{"target":"schools","relation":"beyond","from":"facilities","distance_m":3000}}
"Which school has the worst access to healthcare?" -> {"operation":"find","params":{"target":"schools","relation":"all","from":"facilities","sort":"farthest","limit":1}}
"Show me schools and medical facilities on a map" -> {"operation":"find","params":{"target":"all"}}
"What is the nearest hospital to this area?" -> {"operation":"find","params":{"target":"hospitals","relation":"all","from":"centre","sort":"nearest","limit":1}}
"Which healthcare facilities are closest to me?" -> {"operation":"find","params":{"target":"facilities","relation":"all","from":"me","sort":"nearest","limit":5}}
"Show me all schools within 3 km of this location" -> {"operation":"find","params":{"target":"schools","relation":"within","from":"pin","distance_m":3000}}
"Show me hospitals within a 5 km radius" -> {"operation":"find","params":{"target":"hospitals","relation":"within","from":"pin","distance_m":5000}}
"Which areas have schools but no medical facility within 2 km?" -> {"operation":"coverage","params":{"target":"facilities","distance_m":2000}}
"Which areas have poor access to healthcare?" -> {"operation":"coverage","params":{"target":"facilities"}}
"How far is the nearest school from each neighbourhood?" -> {"operation":"distance_grid","params":{"target":"schools"}}
"Are there enough schools in this neighbourhood?" -> {"operation":"summary","params":{}}
"What's the weather today?" -> {"operation":"unsupported","params":{}}"""


# ---------------------------------------------------------------------------
# Job 1: route
# ---------------------------------------------------------------------------
def route(question: str, place: str, featured_names: dict[str, str] | None = None) -> tuple[dict, str]:
    """Returns (intent, how) where how is "ai" or "keywords"."""
    client = _groq()
    if client is not None:
        try:
            resp = client.chat.completions.create(
                model=MODEL, temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": ROUTER_PROMPT.replace("__PLACE__", place or "the chosen area")},
                          {"role": "user", "content": question}])
            return normalize(json.loads(resp.choices[0].message.content), question), "ai"
        except Exception as exc:          # includes the free tier's 429 "rate limited"
            print(f"[ai] routing fell back to keywords: {exc}")
    return normalize(local_route(question, featured_names), question), "keywords"


def normalize(intent: dict, question: str) -> dict:
    """Clean the model's JSON into known values, and correct common slips using the question's wording."""
    op = intent.get("operation") if isinstance(intent, dict) and intent.get("operation") in OPERATIONS else "summary"
    p = (intent.get("params") if isinstance(intent, dict) else None) or {}
    t = question.lower()
    text = lambda v: "" if v is None else str(v).lower()

    def kind(v):
        v = text(v)
        return "hospitals" if "hosp" in v else "schools" if "school" in v else "all" if re.search(r"all|both", v) else "facilities"

    out: dict = {}
    if p.get("target"):
        out["target"] = kind(p["target"])
    if p.get("from"):
        f = text(p["from"])
        out["from"] = ("me" if re.search(r"^me$|user|\bmy\b", f) else "pin" if re.search(r"pin|location|point|here", f)
                       else "centre" if re.search(r"cent|area", f) else "facilities" if kind(f) == "all" else kind(f))
    if p.get("relation"):
        r = text(p["relation"])
        out["relation"] = "within" if re.search(r"within|inside|near", r) else "beyond" if re.search(r"beyond|more|far|outside", r) else "all"
    if p.get("sort"):
        out["sort"] = "farthest" if "far" in text(p["sort"]) else "nearest"
    if p.get("output"):
        out["output"] = "count" if "count" in text(p["output"]) else "list"
    try:
        d = float(p.get("distance_m"))
        if d > 0:
            out["distance_m"] = d * 1000 if d < 50 else d     # guard against km sent as metres
    except (TypeError, ValueError):
        pass
    try:
        lim = int(p.get("limit"))
        if lim > 0:
            out["limit"] = lim
    except (TypeError, ValueError):
        pass
    if isinstance(p.get("place"), str) and p["place"].strip():
        out["place"] = p["place"].strip()

    if op == "find":
        out.setdefault("target", "schools")
        if out.get("relation") in ("within", "beyond") and not out.get("from"):
            out["from"] = "facilities" if out["target"] == "schools" else "schools"
        # Guard rail 1: "Show me ..." or "closest to medical facilities" is not about the user's position.
        if out.get("from") == "me" and not ME_RE.search(t):
            if re.search(r"this location|\bhere\b|radius|this point|\bpin\b", t):
                out["from"] = "pin"
            elif out["target"] == "schools" and re.search(r"facilit|clinic|hospital|medical|health", t):
                out["from"] = "hospitals" if "hospital" in t else "facilities"
            elif out["target"] != "schools" and "school" in t:
                out["from"] = "schools"
            else:
                out["from"] = "centre"
        # Guard rail 2: "show schools and facilities on a map" means show everything.
        if (re.search(r"\b(show|display|map)\b", t) and "school" in t and re.search(r"facilit|clinic|hospital|medical", t)
                and not re.search(r"\d", t) and not re.search(r"within|beyond|more than|closest|nearest", t)):
            out["target"] = "all"
    return {"operation": op, "params": out}


def local_route(question: str, featured_names: dict[str, str] | None = None) -> dict:
    """Keyword fallback used when the AI can't be reached. Produces the same blocks."""
    t = question.lower()
    km = re.search(r"(\d+(?:\.\d+)?)\s*(km|kilomet)", t)
    m = re.search(r"(\d+)\s*(m\b|meter|metre)", t)
    dm = float(km.group(1)) * 1000 if km else float(m.group(1)) if m else None
    place = None
    for key, name in (featured_names or {}).items():
        if key in t:
            place = name
    health = bool(re.search(r"hospital|clinic|medical|health|facilit|doctor|dentist", t))

    if not re.search(r"school|hospital|clinic|medical|health|facilit|doctor|buffer|access|coverage|area|neighbo|map|distance|near|far|closest", t):
        return {"operation": "unsupported", "params": {}}
    target = "hospitals" if "hospital" in t else "schools" if "school" in t and not re.search(r"(facilit|clinic).*within.*school", t) else "facilities"
    frm = "me" if ME_RE.search(t) else "pin" if re.search(r"this location|\bhere\b|\bpin\b|radius", t) else "centre" if re.search(r"this area|centre|center", t) else None
    base = {"place": place} if place else {}

    if re.search(r"enough|summar|overview|density", t):
        return {"operation": "summary", "params": base}
    if re.search(r"each neighbo|grid|heat ?map|distance map", t):
        return {"operation": "distance_grid", "params": {"target": "schools" if "school" in t else "facilities", **base}}
    if re.search(r"gap|poor access|no (medical|health|hospital|clinic)|areas", t):
        return {"operation": "coverage", "params": {"target": "facilities", "distance_m": dm, **base}}
    if frm:
        return {"operation": "find", "params": {"target": target, "relation": "within" if dm else "all", "from": frm,
                                                "distance_m": dm, "sort": "nearest", "limit": 1 if re.search(r"\bnearest\b", t) else 5, **base}}
    if re.search(r"worst|isolated|underserved|farthest|furthest", t):
        return {"operation": "find", "params": {"target": "schools", "relation": "all", "from": "facilities", "sort": "farthest", "limit": 1, **base}}
    if re.search(r"more than|beyond|farther|further|over", t) and dm:
        return {"operation": "find", "params": {"target": "schools", "relation": "beyond", "from": "facilities", "distance_m": dm, **base}}
    if re.search(r"within|buffer|inside|fall", t):
        fac_first = bool(re.search(r"(facilit|clinic|hospital)\w*\s+(are\s+)?within", t))
        return {"operation": "find", "params": {"target": ("hospitals" if target == "hospitals" else "facilities") if fac_first else "schools",
                                                "relation": "within", "from": "schools" if fac_first else "facilities", "distance_m": dm, **base}}
    if re.search(r"closest|nearest", t):
        return {"operation": "find", "params": {"target": "schools", "relation": "all", "from": "facilities", "sort": "nearest", "limit": 5, **base}}
    if re.search(r"how many|number of|count", t):
        if "school" in t and health:
            return {"operation": "summary", "params": base}
        return {"operation": "find", "params": {"target": target, "relation": "all", "output": "count", **base}}
    if re.search(r"show|map", t):
        return {"operation": "find", "params": {"target": "all", **base}}
    return {"operation": "summary", "params": base}


# ---------------------------------------------------------------------------
# Job 2: explain
# ---------------------------------------------------------------------------
def explain(question: str, facts: str, place: str, source: str) -> tuple[str, str]:
    """Returns (answer, how). Falls back to the computed facts if the AI is unavailable."""
    client = _groq()
    if client is None:
        return facts, "computed"
    try:
        resp = client.chat.completions.create(
            model=MODEL, temperature=0.3,
            messages=[
                {"role": "system", "content":
                    f"You are a GIS analyst for {place}. Explain the analysis result in 1-3 friendly sentences. "
                    "Use PLAIN TEXT ONLY: no markdown, no asterisks, no bold, no bullet points. "
                    "Use ONLY the facts given: never add names, numbers, benchmarks or standards that are not in them. "
                    f"If the facts list names, mention up to 5 of them. The data source is {source}; "
                    '"medical facilities" means hospitals, clinics, medical centres and dentists.'},
                {"role": "user", "content": f"Question: {question}\nFacts: {facts}"}])
        return resp.choices[0].message.content.strip(), "ai"
    except Exception as exc:
        print(f"[ai] explanation fell back to computed text: {exc}")
        return facts, "computed"
