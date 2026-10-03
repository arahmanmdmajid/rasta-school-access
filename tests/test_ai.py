"""The AI layer: guard rails on the model's JSON, and the keyword fallback."""
import json
from pathlib import Path

import pytest

from rasta import ai

RAW = json.loads((Path(__file__).parent / "router_raw.json").read_text(encoding="utf-8"))
TESTER_QUESTIONS = [
    "How many schools are in Gulberg, Lahore?", "How many medical facilities are in Gulberg, Lahore?",
    "Which schools are within 2 km of medical facilities?", "Which medical facilities are within 2 km of schools?",
    "Which schools are closest to medical facilities?", "Which schools are more than 3 km from a medical facility?",
    "Show me schools and medical facilities on a map.", "Create a 2 km buffer around medical facilities.",
    "Which schools fall within the medical-facility buffer?",
    "Which areas have schools but no medical facility within 2 km?", "What is the nearest hospital to this area?",
    "Show me all schools within 3 km of this location.", "Which healthcare facilities are closest to me?",
    "Are there enough schools in this neighborhood?", "Show me hospitals within a 5 km radius.",
    "Which areas have poor access to healthcare?", "Find schools near this location.",
    "How far is the nearest school from each neighborhood",
]


def test_show_me_is_not_my_location():
    raw = {"operation": "find", "params": {"target": "schools", "relation": "all", "from": "me"}}
    fixed = ai.normalize(raw, "Show me schools and medical facilities on a map.")
    assert fixed["params"]["target"] == "all"


def test_closest_to_facilities_measures_from_facilities():
    raw = {"operation": "find", "params": {"target": "schools", "relation": "all", "from": "me", "sort": "nearest"}}
    assert ai.normalize(raw, "Which schools are closest to medical facilities?")["params"]["from"] == "facilities"


def test_this_location_means_pin():
    raw = {"operation": "find", "params": {"target": "hospitals", "relation": "within", "from": "me", "distance_m": 5000}}
    assert ai.normalize(raw, "Show me hospitals within a 5 km radius.")["params"]["from"] == "pin"


def test_real_near_me_is_kept():
    raw = {"operation": "find", "params": {"target": "facilities", "from": "me"}}
    assert ai.normalize(raw, "Which healthcare facilities are closest to me?")["params"]["from"] == "me"


def test_km_sent_as_metres_is_corrected():
    raw = {"operation": "find", "params": {"target": "schools", "relation": "within", "distance_m": 2}}
    assert ai.normalize(raw, "within 2 km")["params"]["distance_m"] == 2000


@pytest.mark.parametrize("question", TESTER_QUESTIONS)
def test_keyword_router_answers_every_tester_question(question):
    assert ai.local_route(question)["operation"] != "unsupported"


def test_keyword_router_refuses_off_topic():
    assert ai.local_route("What's the weather today?")["operation"] == "unsupported"


@pytest.mark.parametrize("item", RAW, ids=lambda i: i["q"][:40])
def test_real_model_answers_normalize_to_valid_blocks(item):
    if "error" in item["raw"]:
        pytest.skip("rate limited during the original recording")
    n = ai.normalize(item["raw"], item["q"])
    assert n["operation"] in ai.OPERATIONS


def test_route_without_key_uses_keywords():
    intent, how = ai.route("Which areas have poor access to healthcare?", "Ghirnatah District, Riyadh")
    assert how == "keywords" and intent["operation"] == "coverage"
