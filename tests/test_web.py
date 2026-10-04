"""
Contract checks on the two pages.

Crude - string checks over HTML - but each one pins a bug that actually shipped and that
no Python test could have caught. The pages are self-contained by design, so there is no
build step to assert against.
"""

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
MAP = WEB / "map.html"
LANDING = WEB / "index.html"


@pytest.fixture(scope="module")
def mp() -> str:
    return MAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def land() -> str:
    return LANDING.read_text(encoding="utf-8")


# --------------------------------------------------------------- the map page

def test_hidden_actually_hides_the_ask_window(mp):
    """
    The window is styled with an id selector, which outranks the browser's own
    [hidden]{display:none}. Without an explicit rule the close button flips the
    attribute and nothing moves - the window stays on screen looking stuck.
    """
    assert "#askwin[hidden]{display:none}" in mp.replace(" ", "")


def test_bundles_are_fetched_with_a_cache_buster(mp):
    """
    force-cache served a stale bundle forever, so a rebuild never reached anyone who had
    opened the page before - and the choropleth silently drew nothing.
    """
    assert 'cache:"force-cache"' not in mp.replace(" ", "")
    assert "?v=${IDX.built}" in mp


def test_map_builds_hexagons_from_the_template(mp):
    assert "hex_offsets_m" in mp
    assert "CELLS.cls" in mp
    assert "BUNDLE.render" not in mp, "polygons are no longer shipped"


def test_api_url_points_at_our_own_service(mp):
    """
    The first guessed Render hostname belonged to somebody else's live service. Pointing
    at it would have sent users' questions to a stranger.
    """
    assert "rasta-api-2019.onrender.com" in mp
    assert '"https://rasta-api.onrender.com"' not in mp


def test_school_markers_do_not_swallow_the_hover(mp):
    assert "interactive:false" in mp.replace(" ", "")


def test_map_carries_data_attribution(mp):
    for credit in ("Esri", "UNICEF Giga", "OpenStreetMap", "ODbL"):
        assert credit in mp, f"missing {credit} attribution on the map"


def test_map_links_back_to_the_story(mp):
    assert 'href="index.html"' in mp


def test_the_field_brief_is_a_real_artefact(mp):
    """
    The deck claims Rasta automates the district prioritisation memo. For a while that
    memo was a paragraph in a chat panel - nothing an officer could file or take out.
    It is now a downloadable document, and these are the parts that make it one.
    """
    assert 'id="brief"' in mp and "downloadBrief" in mp
    assert "Read this first" in mp, "the brief must lead with the data caveat"
    assert "Verified on site?" in mp, "no column to tick in the field"
    assert "openstreetmap.org/?mlat=" in mp, "coordinates are not locatable"
    # A standalone file opens in someone else's browser and theme.
    assert "background:#ffffff" in mp.replace(" ", ""), "no explicit background"
    for part in ("Method", "UNESCO", "ODbL", "Modelled estimates"):
        assert part in mp, f"brief omits {part}"


def test_legend_sits_on_the_map_and_can_be_dismissed(mp):
    """
    Bottom-left: zoom controls own the top-left and the ask button owns the bottom-right.
    It must collapse, because on a phone a legend that cannot be dismissed is just
    something covering the map.
    """
    flat = mp.replace(" ", "")
    assert 'id="legendbox"' in mp and 'id="legendtoggle"' in mp
    assert "#legendbox{position:absolute;left:14px" in flat
    assert "#legendbox.closed#legendpanel{display:none}" in flat
    assert "#legendbox.closed#legendtoggle{display:grid}" in flat
    assert 'rasta-legend' in mp, "the open/closed choice is not remembered"
    assert '<div id="legend"></div>' in mp and "#panel #legend" not in mp


# ----------------------------------------------------------- the landing page

def test_landing_is_the_space_entry_point():
    """HF static Spaces serve index.html; the story is the front door, the map is a click away."""
    assert LANDING.exists() and MAP.exists()
    readme = (WEB / "README.md").read_text(encoding="utf-8")
    assert "app_file: index.html" in readme
    assert "sdk: static" in readme


def test_landing_sends_people_to_the_map(land):
    assert 'href="map.html"' in land


def test_landing_has_every_story_step(land):
    for n in range(1, 8):
        assert f'data-step="{n}"' in land, f"story step {n} is missing"


def test_landing_stays_within_its_size_budget():
    """Under 300 KB excluding data, and it should not creep."""
    assert LANDING.stat().st_size < 300_000


def test_landing_loads_no_javascript_libraries(land):
    """Vanilla only: IntersectionObserver, no framework, no charting library."""
    for banned in ("d3.", "gsap", "scrollama", "jquery", "react", "leaflet"):
        assert banned not in land.lower(), f"{banned} should not be on the landing page"
    assert "IntersectionObserver" in land


def test_landing_is_readable_without_javascript(land):
    """
    The story must survive a failed script. The body ships with .nojs, which reveals the
    end state, and the first thing the script does is remove it.
    """
    assert 'class="nojs"' in land
    assert 'classList.remove("nojs")' in land
    assert ".nojs .lay{opacity:1}" in land.replace("\n", "")


def test_landing_respects_reduced_motion(land):
    assert "prefers-reduced-motion" in land


def test_landing_has_open_graph_tags(land):
    for prop in ("og:title", "og:description", "og:type", "og:url", "twitter:card"):
        assert prop in land, f"missing {prop}"


def test_landing_describes_the_visual_for_screen_readers(land):
    assert 'role="img"' in land and "aria-label=" in land
    assert 'class="skip"' in land, "no skip link past the story"


def test_landing_states_the_data_caveats(land):
    """The completeness caveat is the most important sentence on the site."""
    for phrase in ("field-verify", "13%", "769", "UNESCO", "ODbL"):
        assert phrase in land, f"data note is missing {phrase}"


def test_readme_describes_what_the_project_actually_is():
    """
    The README drifted once already: it still called index.html the map after the app
    moved to map.html, and listed neither of the two newest operations.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for claim in ("map.html", "`explain`", "`poorest`", "scrollytelling"):
        assert claim in readme, f"README does not mention {claim}"
    assert "index.html          landing page + map" not in readme, "stale repo layout"


def test_readme_diagram_colours_the_agents_by_kind():
    """
    Amber for the two language models, teal for the four deterministic agents. The point
    of the picture is that neither model has the final word, so the split has to be
    visible rather than only described in the caption.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "classDef llm" in readme and "classDef code" in readme
    assert "class P,W llm" in readme, "the planner and writer are the LLM agents"
    assert "class D,A,E,V code" in readme, "the other four are deterministic"
    # The veto edge is the whole argument; a plain chain of six boxes is a weaker claim.
    assert "V -.->" in readme, "the verifier's feedback edge is missing"


def test_landing_figures_match_the_built_data():
    """
    The headline numbers are baked into the page. If the bundles are rebuilt and the
    totals move, this fails rather than letting the story quietly drift from the data.
    """
    import json
    total = 0
    for path in (WEB / "districts").glob("PK*.json"):
        total += json.loads(path.read_text(encoding="utf-8"))["totals"]["children_underserved_est"]
    land = LANDING.read_text(encoding="utf-8")
    assert f"const SINDH_BEYOND = {total};" in land, (
        f"page says something else; districts now sum to {total:,}")
