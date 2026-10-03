"""
Contract checks on the page itself.

These are crude - string checks over one HTML file - but each one pins a bug that
actually shipped and that no Python test could have caught. The page is a single
self-contained file by design, so there is no build step to assert against.
"""

from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_hidden_actually_hides_the_ask_window(html):
    """
    The window is styled with an id selector, which outranks the browser's own
    [hidden]{display:none}. Without an explicit rule the close button flips the
    attribute and nothing moves - the window stays on screen looking stuck.
    """
    assert "#askwin[hidden]{display:none}" in html.replace(" ", "")


def test_bundles_are_fetched_with_a_cache_buster(html):
    """
    force-cache served a stale bundle forever, so a rebuild never reached anyone who
    had opened the page before - and the choropleth silently drew nothing.
    """
    assert 'cache:"force-cache"' not in html.replace(" ", "")
    assert "?v=${IDX.built}" in html


def test_page_builds_hexagons_from_the_template(html):
    assert "hex_offsets_m" in html
    assert "CELLS.cls" in html
    assert "BUNDLE.render" not in html, "polygons are no longer shipped"


def test_api_url_points_at_our_own_service(html):
    """
    The first guessed Render hostname belonged to somebody else's live service. Pointing
    at it would have sent users' questions to a stranger.
    """
    assert "rasta-api-2019.onrender.com" in html
    assert '"https://rasta-api.onrender.com"' not in html


def test_school_markers_do_not_swallow_the_hover(html):
    """Interactive markers punch dead spots in the cursor readout."""
    assert "interactive:false" in html.replace(" ", "")


def test_map_carries_data_attribution(html):
    for credit in ("Esri", "UNICEF Giga", "OpenStreetMap", "ODbL"):
        assert credit in html, f"missing {credit} attribution on the map"
