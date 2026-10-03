"""The analyses must reproduce the numbers measured on the original app."""
import pytest

from rasta import analysis, data


@pytest.mark.parametrize("key, schools, facilities", [
    ("ghirnatah", 8, 16), ("gulberg", 79, 59), ("clifton", 54, 35)])
def test_featured_counts(key, schools, facilities):
    area = data.load_featured(key)
    assert (len(area.schools), len(area.facilities)) == (schools, facilities)


def test_gulberg_schools_within_250m():
    r = analysis.find(data.load_featured("gulberg"),
                      {"target": "schools", "relation": "within", "from": "facilities", "distance_m": 250})
    assert len(r["items"]) == 43


def test_ghirnatah_coverage_500m_matches_original():
    r = analysis.coverage(data.load_featured("ghirnatah"), {"target": "facilities", "distance_m": 500})
    assert r["percent"] == 39                  # original grid estimate was 39%
    assert len(r["schools_in_gap"]) == 3


def test_worst_school_and_average():
    s = analysis.summary(data.load_featured("ghirnatah"))
    assert s["worst"]["name"] == "Growing Minds Academy"
    assert 810 < s["worst"]["dist"] < 830
    assert "506 m" in s["text"]


def test_no_school_beyond_2km_in_featured_districts():
    for key in ("ghirnatah", "gulberg", "clifton"):
        r = analysis.find(data.load_featured(key),
                          {"target": "schools", "relation": "beyond", "from": "facilities", "distance_m": 2000})
        assert r["items"] == []


def test_default_distance_adapts_to_density():
    assert analysis.default_distance(data.load_featured("ghirnatah")) == 500
    assert analysis.default_distance(data.load_featured("gulberg")) == 250


def test_pin_and_centre_references():
    area = data.load_featured("ghirnatah")
    with_pin = analysis.find(area, {"target": "schools", "relation": "within", "from": "pin", "distance_m": 1000},
                             pin=[46.745, 24.79])
    assert with_pin["ref"]["label"] == "your map pin"
    no_pin = analysis.find(area, {"target": "schools", "relation": "all", "from": "pin"})
    assert "No pin is set" in no_pin["text"]


def test_distance_grid_builds_cells():
    r = analysis.distance_grid(data.load_featured("ghirnatah"), {"target": "schools"})
    assert 400 < len(r["cells"]) < 800
    assert r["legend"]
