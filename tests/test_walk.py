"""
The walk model.

Everything Rasta claims rests on these three constants and two functions, so they are
tested before anything else. In particular, dividing by the detour factor where you
should multiply (or vice versa) is a 69% error that no other test in the suite would
notice - the map would still render, the shortlist would still rank, and every number
on screen would be wrong.
"""

import pytest

from rasta import config


def test_walk_minutes_round_trip():
    """radius_m and walk_minutes must be exact inverses at the UNESCO threshold."""
    # 15 min at 4 km/h with a 1.3 detour factor = (15/60 * 4000) / 1.3 = 769.23 m
    radius = config.walk_radius_m(15)
    assert radius == pytest.approx(769.23, abs=0.5), (
        f"expected ~769 m reachable in 15 min, got {radius:.1f} m. "
        "A value near 1300 means the detour factor is multiplied where it should divide."
    )
    assert config.walk_minutes(radius) == pytest.approx(15.0, abs=1e-9)


def test_default_radius_matches_default_threshold():
    """The no-argument call is the one the browser draws; it must use the threshold."""
    assert config.walk_radius_m() == pytest.approx(config.walk_radius_m(config.THRESHOLD_MIN))


def test_detour_factor_makes_walking_slower_not_faster():
    """
    A detour factor >1 means the real route is longer than the straight line, so a
    given straight-line distance takes MORE time, and a given time budget reaches
    LESS far than speed alone would suggest.
    """
    assert config.DETOUR_FACTOR > 1.0
    straight_line_only = (config.THRESHOLD_MIN / 60) * config.WALK_KMH * 1000
    assert config.walk_radius_m() < straight_line_only


def test_walk_monotonic_and_zero_at_zero():
    assert config.walk_minutes(0) == 0
    assert config.walk_radius_m(0) == 0
    distances = [0, 100, 500, 769.23, 1000, 5000]
    minutes = [config.walk_minutes(d) for d in distances]
    assert minutes == sorted(minutes)
    assert all(b > a for a, b in zip(minutes, minutes[1:]))


@pytest.mark.parametrize("minutes", [5, 15, 30, 45, 60, 120])
def test_inverse_holds_across_the_bands(minutes):
    """Round-tripping must hold at every band edge we classify on, not just at 15."""
    assert config.walk_minutes(config.walk_radius_m(minutes)) == pytest.approx(minutes)


def test_bands_are_sorted_and_start_at_the_threshold():
    assert list(config.BANDS_MIN) == sorted(config.BANDS_MIN)
    assert config.BANDS_MIN[0] == config.THRESHOLD_MIN


def test_every_tunable_parameter_carries_a_citation():
    """
    A judge is entitled to ask where any number came from. If a parameter is added
    without a source, this test fails rather than letting it reach the UI unexplained.
    """
    for name in ("WALK_KMH", "DETOUR_FACTOR", "THRESHOLD_MIN", "BANDS_MIN", "GIRLS_PENALTY"):
        assert name in config.CITATIONS, f"{name} has no citation"
        assert len(config.CITATIONS[name]) > 60, f"{name} citation is too thin to be useful"
