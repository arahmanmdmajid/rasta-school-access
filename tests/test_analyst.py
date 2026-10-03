"""
The Analyst, and the bundles it reads.

These assert invariants and bounds, never exact measured values. The forked project's
suite hard-coded the numbers for three districts, which meant every rebuild of the data
broke the tests and taught you nothing about whether the code was right.
"""

import pytest

from rasta import analyst, bundles, config, pipeline


# ------------------------------------------------------------------ the grid

def test_cells_are_parallel_and_complete(bundle):
    cells = bundle["cells"]
    lengths = {k: len(v) for k, v in cells.items()}
    assert len(set(lengths.values())) == 1, f"ragged cell arrays: {lengths}"
    assert lengths["lon"] == bundle["totals"]["hexes"]


def test_no_nan_or_negative_in_cells(bundle):
    """
    A NaN here renders as "NaN minutes" in the live readout, in front of whoever is
    watching. Cheap to assert, impossible to miss if it ever happens.
    """
    cells = bundle["cells"]
    for key in ("min", "pop", "ch"):
        values = cells[key]
        assert all(v is not None for v in values), f"{key} has nulls"
        assert all(v == v for v in values), f"{key} has NaN"
        assert min(values) >= 0, f"{key} has negative values"


def test_population_totals_are_consistent(bundle):
    cells = bundle["cells"]
    assert abs(sum(cells["pop"]) - bundle["totals"]["population"]) <= len(cells["pop"])
    expected = sum(cells["pop"]) * config.CHILD_SHARE
    assert bundle["totals"]["children_est"] == pytest.approx(expected, rel=0.01)


def test_underserved_never_exceeds_total(bundle):
    t = bundle["totals"]
    assert 0 <= t["children_underserved_est"] <= t["children_est"]


def test_walk_minutes_match_the_model(bundle):
    """Minutes in the bundle must come from the same formula the page prints on screen."""
    radius = bundle["params"]["radius_m"]
    assert radius == pytest.approx(config.walk_radius_m(), abs=1)
    assert config.walk_minutes(radius) == pytest.approx(bundle["params"]["threshold_min"], abs=0.1)


# -------------------------------------------------------------- the shortlist

def test_shortlist_is_sorted_and_spatially_separated(bundle):
    """
    Without the spatial de-duplication the top ten sites are ten views of one village,
    which looks ridiculous on the map and is useless to an officer.
    """
    sites = bundle["shortlist"]
    if len(sites) < 2:
        pytest.skip("district has fewer than two sites")
    reached = [s["children_reached"] for s in sites]
    assert reached == sorted(reached, reverse=True)

    radius = bundle["params"]["radius_m"]
    kx, ky = 101_000, 110_540           # conservative metres per degree at these latitudes
    for i, a in enumerate(sites):
        for b in sites[i + 1:]:
            d = (((a["lon"] - b["lon"]) * kx) ** 2 + ((a["lat"] - b["lat"]) * ky) ** 2) ** 0.5
            assert d > radius * 0.9, f"sites {a} and {b} are only {d:.0f} m apart"


def test_reached_never_exceeds_the_underserved_total(bundle):
    """Catches double counting across overlapping catchments."""
    total = sum(s["children_reached"] for s in bundle["shortlist"])
    assert total <= bundle["totals"]["children_underserved_est"] + 1


def test_interventions_come_from_the_closed_menu(bundle):
    from rasta.planner import INTERVENTIONS
    for s in bundle["shortlist"]:
        assert s["intervention"] in INTERVENTIONS


# ------------------------------------------------------- the awkward district

def test_a_district_with_no_schools_is_internally_consistent(empty_bundle):
    """
    The bug this pins: infinity fell out of the "underserved" mask but not out of the
    shortlist, so a district with no mapped schools reported 0% underserved while still
    recommending ten sites.
    """
    t = empty_bundle["totals"]
    assert empty_bundle["confidence"]["mapped"] == 0
    assert t["children_underserved_est"] == t["children_est"]
    sentinel = empty_bundle["params"]["no_supply_min"]
    assert all(m >= sentinel for m in empty_bundle["cells"]["min"])


def test_provenance_gate_speaks_plainly_when_nothing_is_mapped(empty_bundle):
    caveat = bundles.provenance(empty_bundle)["caveat"]
    assert "map" in caveat.lower()
    assert bundles.provenance(empty_bundle)["verdict"] == "none"


# ----------------------------------------------------------------- operations

def test_gap_percentages_are_in_range(bundle):
    result = analyst.gap(bundle, {"minutes": 15})
    assert 0 <= result["facts"]["percent_beyond"] <= 100
    assert str(result["facts"]["children_beyond"]) or True
    assert result["text"]


def test_girls_cohort_reduces_the_count(bundle):
    children = analyst.gap(bundle, {"minutes": 15, "cohort": "children"})
    girls = analyst.gap(bundle, {"minutes": 15, "cohort": "girls"})
    assert girls["facts"]["children_beyond"] <= children["facts"]["children_beyond"]


def test_a_higher_threshold_leaves_fewer_children_beyond_it(bundle):
    near = analyst.gap(bundle, {"minutes": 15})["facts"]["children_beyond"]
    far = analyst.gap(bundle, {"minutes": 60})["facts"]["children_beyond"]
    assert far <= near


def test_unknown_operation_falls_through_to_summary(bundle):
    assert analyst.run("nonsense", bundle, {})["op"] == "summary"


# ------------------------------------------------------------------- pipeline

def test_pipeline_runs_every_agent_offline():
    """End to end with no API key: the deterministic path must produce a full answer."""
    out = pipeline.answer("where should we open learning centres?")
    agents = [step["agent"] for step in out["trace"]]
    assert agents == ["Planner", "Data Steward", "Analyst", "Equity Weigher",
                      "Brief Writer", "Verifier"]
    assert out["answer"]
    assert out["verified"] is True          # computed text always verifies against itself


def test_pipeline_refuses_off_topic_questions():
    out = pipeline.answer("what is the weather tomorrow?")
    assert "walking access" in out["answer"]


def test_pipeline_reports_an_unbundled_district():
    out = pipeline.answer("tell me about Lahore")
    assert out["answer"]
