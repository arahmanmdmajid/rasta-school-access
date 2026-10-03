"""
Agent 1 - the Planner.  LLM.

Turns a plain-English question into one analysis request. It chooses an operation and
fills parameters; it never computes anything and never sees a number it could repeat.

Two things make this safe. The model is asked for JSON only, and `normalize()` is the
real schema: it whitelists keys, coerces every value into a closed set, and silently
drops anything the model invents. And `local_route()` is a total keyword fallback, so a
missing key, a 429 or a retired model degrades the answer rather than breaking it.
"""

from __future__ import annotations

import json
import re

from . import ai

OPERATIONS = ("gap", "shortlist", "summary", "compare", "unsupported")
INTERVENTIONS = ("ncl", "rehab", "annexe", "route", "any")
COHORTS = ("children", "girls")

MIN_MINUTES, MAX_MINUTES = 1, 180
MAX_LIMIT = 20

PROMPT = """\
You turn a question about walking access to school in Pakistan into ONE analysis block.
Return ONLY JSON: {"operation": "...", "params": {...}}.

operation is one of:
  gap        - where children live beyond a walking threshold of a school
  shortlist  - which sites should be visited or invested in first
  summary    - an overview of a district
  compare    - set two or more districts against each other
  unsupported- anything not about school access, population or this analysis

params may contain ONLY these keys:
  minutes      integer walking threshold in MINUTES (not metres, not kilometres)
  district     a named district, exactly as the user wrote it
  intervention one of ncl (non-formal learning centre), rehab (repair a damaged school),
               annexe (girls' annexe at an existing school), route (transport or safe route), any
  cohort       children or girls
  limit        how many results, 1 to 20

Rules:
  Distances are always expressed in MINUTES of walking. "15" means 15 minutes.
  "where should we build", "which sites", "recommend", "prioritise" -> shortlist.
  "how far", "who is far", "underserved", "beyond" -> gap.
  Mention district ONLY when the user names one.
  Use cohort "girls" only when the user asks about girls specifically.

Examples:
  "where are children more than 30 minutes from a school?" -> {"operation":"gap","params":{"minutes":30}}
  "which 5 sites should we open learning centres in?" -> {"operation":"shortlist","params":{"limit":5,"intervention":"ncl"}}
  "where should we invest first in Tharparkar?" -> {"operation":"shortlist","params":{"district":"Tharparkar"}}
  "how many girls are beyond a 15 minute walk?" -> {"operation":"gap","params":{"minutes":15,"cohort":"girls"}}
  "tell me about Dadu" -> {"operation":"summary","params":{"district":"Dadu"}}
  "compare Malir and Tharparkar" -> {"operation":"compare","params":{}}
  "what's the weather tomorrow?" -> {"operation":"unsupported","params":{}}
"""

# A question of this shape is a request for recommendations whatever the model says.
# Keyword override of a model choice is a pattern that earns its place: the model
# reliably answers "gap" to questions that are plainly asking "where should we go".
WANTS_SHORTLIST = re.compile(
    r"where should|which sites?|recommend|priorit|shortlist|invest|intervention|open (a |an )?(centre|center|school)",
    re.I,
)
OFF_TOPIC = re.compile(r"weather|joke|recipe|football|cricket score|stock|bitcoin", re.I)


def route(question: str, districts: list[str] | None = None) -> tuple[dict, str]:
    """Returns (intent, how) where how is "ai" or "keywords" or "keywords (<reason>)"."""
    if ai.available():
        try:
            raw = ai.chat(PROMPT, question, as_json=True, temperature=0)
            return normalize(json.loads(raw), question), "ai"
        except Exception as exc:
            return (normalize(local_route(question, districts), question),
                    f"keywords ({ai.error_reason(exc)})")
    # The district list has to reach the fallback, or every question silently lands on
    # whichever district happens to be first.
    return normalize(local_route(question, districts), question), "keywords"


def normalize(intent: dict, question: str) -> dict:
    """
    The real schema. Anything the model returns that is not recognised here is dropped.
    """
    if not isinstance(intent, dict):
        intent = {}
    op = intent.get("operation")
    op = op if op in OPERATIONS else "summary"
    raw = intent.get("params")
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}

    # Minutes. There is deliberately NO unit heuristic here: the forked code silently
    # read any value under 50 as kilometres, which would have turned our 15-minute
    # threshold into 15,000. A plausibility range is the only check.
    try:
        minutes = float(raw.get("minutes"))
    except (TypeError, ValueError):
        minutes = None
    if minutes is not None and MIN_MINUTES <= minutes <= MAX_MINUTES:
        out["minutes"] = int(round(minutes))

    if isinstance(raw.get("district"), str) and raw["district"].strip():
        # Resolving the name against what is actually bundled happens in the API layer,
        # so this module never needs to import the data layer.
        out["district"] = raw["district"].strip()[:60]

    intervention = str(raw.get("intervention") or "").lower()
    if intervention:
        out["intervention"] = (
            "ncl" if re.search(r"ncl|non.?formal|centre|center|learning", intervention)
            else "rehab" if re.search(r"rehab|flood|repair|damag", intervention)
            else "annexe" if re.search(r"annex|girl", intervention)
            else "route" if re.search(r"route|transport|bus|safe", intervention)
            else "any"
        )

    cohort = str(raw.get("cohort") or "").lower()
    if cohort:
        out["cohort"] = "girls" if "girl" in cohort or "female" in cohort else "children"

    try:
        limit = int(raw.get("limit"))
        if limit > 0:
            out["limit"] = min(limit, MAX_LIMIT)
    except (TypeError, ValueError):
        pass

    # Guard rail: a plainly prescriptive question is a shortlist, whatever was returned.
    if op in ("gap", "summary") and WANTS_SHORTLIST.search(question or ""):
        op = "shortlist"
    if op != "unsupported" and OFF_TOPIC.search(question or ""):
        op = "unsupported"

    return {"operation": op, "params": out}


def local_route(question: str, names: list[str] | None = None) -> dict:
    """
    Total keyword fallback: always returns a block, never raises, needs no network.

    Takes names as an argument rather than importing the data layer, so this module
    stays free of any dependency on how districts are stored.
    """
    t = (question or "").lower()
    params: dict = {}

    minutes = re.search(r"(\d{1,3})\s*(?:-|\s)?\s*(?:min|minute)", t)
    if minutes:
        value = int(minutes.group(1))
        if MIN_MINUTES <= value <= MAX_MINUTES:
            params["minutes"] = value

    if re.search(r"\bgirls?\b|\bfemale\b", t):
        params["cohort"] = "girls"

    limit = re.search(r"\b(?:top|first|best)\s+(\d{1,2})\b|\b(\d{1,2})\s+sites?\b", t)
    if limit:
        params["limit"] = min(int(limit.group(1) or limit.group(2)), MAX_LIMIT)

    for name in names or []:
        if name.lower() in t:
            params["district"] = name

    # A comparison is on topic even when the question is only two place names and the
    # word "compare" - nothing in the vocabulary below would otherwise match it.
    comparing = bool(re.search(r"compare|versus|\bvs\b|against|side by side", t))

    if OFF_TOPIC.search(t) or not (comparing or params.get("district") or re.search(
        r"school|child|children|girl|walk|access|far|distance|centre|center|site|district|"
        r"underserved|gap|invest|priorit|recommend|population|poor", t)):
        return {"operation": "unsupported", "params": {}}

    if WANTS_SHORTLIST.search(t):
        return {"operation": "shortlist", "params": params}
    if comparing:
        return {"operation": "compare", "params": params}
    if re.search(r"beyond|more than|further|farther|far from|underserved|gap|how many", t):
        return {"operation": "gap", "params": params}
    return {"operation": "summary", "params": params}


def describe(intent: dict) -> str:
    """A compact label for the block that ran, shown beside the answer so it is auditable."""
    params = ", ".join(f"{k}={v}" for k, v in sorted(intent.get("params", {}).items()))
    return f"{intent.get('operation', '?')}({params})"
