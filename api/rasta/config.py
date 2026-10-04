"""
Rasta — tunable parameters, with the citation for every one of them.

This module imports nothing from the rest of the package, so it is safe to import
anywhere, including before `rasta.ai` freezes its model name at import time.

Every constant here is a modelling choice a judge (or an NGO officer) is entitled to
question, so each one ships with the source it came from. CITATIONS is rendered in the
UI next to the parameter controls and reproduced in the README.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(root: Path | None = None) -> None:
    """
    Read .env at the repo root into the environment, without a python-dotenv dependency.

    Real environment variables win, so a deployed service is never overridden by a file
    that happens to be lying around. Both the API and the build scripts need this, which
    is why it lives here instead of being written out twice.
    """
    root = root or Path(__file__).resolve().parents[2]
    env = root / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --- The walk model -------------------------------------------------------------
# Straight-line distance in metres, inflated by a detour factor, divided by a walking
# speed. Closed form on purpose: it is what makes a live hover catchment possible,
# because the catchment of a point is then simply a circle.

WALK_KMH = 4.0
DETOUR_FACTOR = 1.3
THRESHOLD_MIN = 15
BANDS_MIN = (15, 30, 45, 60)

# --- Turning population into school-age children ---------------------------------
# Kontur gives total population per hexagon, not ages. These shares convert it, and
# they are the weakest link in the chain: they are national averages applied uniformly,
# so a hexagon's child count is an ESTIMATE and is labelled as one everywhere it appears.
CHILD_SHARE = 0.30          # share of population aged roughly 5-16
GIRL_SHARE = 0.48           # share of those children who are girls

# --- Equity ---------------------------------------------------------------------
# No school dataset available for Pakistan carries a gender field (checked: UNICEF Giga,
# OpenStreetMap, Overture). So girls cannot be handled on the supply side. They are
# handled on the demand side instead, which is also closer to what GEM 2026 measured:
# the finding is about girls' ATTENDANCE versus walk time, not about girls' schools.
GIRLS_PENALTY = 0.15

# --- Scope ----------------------------------------------------------------------
# The same pipeline runs for anything people walk to. Only the source query differs;
# the population grid, the walking model, the ranking and the verifier are untouched.
#
# `overture` is matched against Overture's taxonomy.primary and basic_category.
# `giga` says whether UNICEF Giga is a source at all - it is a school database, so it
# contributes nothing to a health build.
FACILITY_TYPE = "education"          # a key of FACILITY_TYPES

FACILITY_TYPES = {
    "education": {
        "label": "school",
        "plural": "schools",
        "giga": True,
        "overture": ("school", "education", "kindergarten"),
    },
    "health": {
        "label": "health facility",
        "plural": "health facilities",
        "giga": False,
        "overture": ("hospital", "clinic", "doctor", "health", "medical", "pharmacy"),
    },
}


def facility(kind: str | None = None) -> dict:
    """The configured facility type, or a named one. Unknown names fall back rather
    than raising, so a stale bundle label can never break a read."""
    return FACILITY_TYPES.get(kind or FACILITY_TYPE, FACILITY_TYPES["education"])

# --- Provenance -----------------------------------------------------------------

CITATIONS = {
    "WALK_KMH": (
        "4.0 km/h (1.11 m/s) is the assumption used in school-travel research for "
        "under-12s. Measured walking speeds for children aged 10-12 are higher "
        "(1.24-1.31 m/s, i.e. 4.5-4.7 km/h), so 4.0 km/h is deliberately conservative: "
        "a slower assumed speed yields longer estimated travel times, so this model "
        "never understates the barrier."
    ),
    "DETOUR_FACTOR": (
        "1.3 is the widely reported circuity (detour) factor - the ratio of network "
        "distance to straight-line distance. Ballou et al. (2002) put the global figure "
        "near 1.3; urban road networks measure 1.2-1.3. Caveat: that literature is "
        "largely about roads and driving. Rural walking circuity may be higher where "
        "few formal paths exist, so this parameter is exposed and adjustable."
    ),
    "THRESHOLD_MIN": (
        "UNESCO Global Education Monitoring Report 2026: girls aged 10-12 who travel "
        "45-60 minutes to school are 15% less likely to attend than peers within 15 "
        "minutes. 15 minutes is therefore the access threshold, not an invented round "
        "number."
    ),
    "BANDS_MIN": (
        "15/30/45/60 minute bands bracket the UNESCO comparison, whose two endpoints "
        "are the under-15-minute group and the 45-60 minute group."
    ),
    "CHILD_SHARE": (
        "0.30 is an approximation of Pakistan's population aged 5-16, applied uniformly "
        "to every hexagon because Kontur supplies total population only. This is the "
        "weakest assumption in the model: real age structure varies between districts "
        "and between urban and rural areas, so every child count is an estimate, never "
        "a census figure."
    ),
    "GIRL_SHARE": (
        "0.48 reflects Pakistan's slightly male-skewed child sex ratio. Applied "
        "uniformly, with the same caveat as CHILD_SHARE."
    ),
    "GIRLS_PENALTY": (
        "0.15 is the attendance gap UNESCO GEM 2026 reports for the 45-60 minute cohort "
        "of girls aged 10-12. Applied as a demand-side weight because no available "
        "school dataset for Pakistan identifies girls' schools."
    ),
}

# --- Data sources, for the attribution block -------------------------------------

SOURCES = {
    "schools": {
        "name": "UNICEF Giga",
        "licence": "ODbL",
        "note": "Partly derived from OpenStreetMap. Credit: Giga and its contributors.",
        "url": "https://maps.giga.global/",
    },
    "population": {
        "name": "Kontur Population Dataset (Pakistan)",
        "licence": "CC-BY",
        "note": "400 m H3 hexagons.",
        "url": "https://data.humdata.org/dataset/kontur-population-pakistan",
    },
    "poverty": {
        "name": "Meta Relative Wealth Index",
        "licence": "CC-BY-NC",
        "note": (
            "A modelled estimate of RELATIVE wealth at ~2.4 km resolution, not measured "
            "income and not a poverty line. Used to rank areas, never to means-test."
        ),
        "url": "https://data.humdata.org/dataset/relative-wealth-index",
    },
    "boundaries": {
        "name": "OCHA Common Operational Datasets - Pakistan admin boundaries",
        "licence": "CC-BY-IGO",
        "note": "District = ADM2.",
        "url": "https://data.humdata.org/dataset/cod-ab-pak",
    },
    "official_counts": {
        "name": "Pakistan Institute of Education, Pakistan Education Statistics",
        "licence": "Government publication",
        "note": (
            "Used as the official denominator for the mapped-school completeness ratio."
        ),
        "url": "https://pie.gov.pk/",
    },
}


def walk_minutes(metres: float) -> float:
    """Estimated walking time in minutes for a straight-line distance in metres."""
    return (metres * DETOUR_FACTOR) / (WALK_KMH * 1000.0) * 60.0


def walk_radius_m(minutes: float = THRESHOLD_MIN) -> float:
    """
    The straight-line radius reachable on foot in `minutes`.

    This is the inverse of walk_minutes, and it is why a catchment is a circle:
    the browser can draw one at cursor speed with no server call.
    """
    return (minutes / 60.0) * (WALK_KMH * 1000.0) / DETOUR_FACTOR
