"""
The Planner's schema.

normalize() is the real contract with the language model: whatever comes back, only
recognised keys with values in closed sets survive. These tests drive it with the kind
of output models actually produce, including the malformed kind.
"""

import pytest

from rasta import planner


def test_normalize_keeps_fifteen_as_fifteen_minutes():
    """
    Regression test for the unit heuristic inherited from the forked code.

    That code read any distance below 50 as kilometres and multiplied by 1000. Our
    threshold is 15 MINUTES, so the same rule would have silently turned it into 15,000
    and every answer in the product would have been wrong in the same direction.
    """
    out = planner.normalize({"operation": "gap", "params": {"minutes": 15}}, "within 15 minutes?")
    assert out["params"]["minutes"] == 15


@pytest.mark.parametrize("raw,expect_missing", [
    ({"operation": "gap", "params": {"minutes": "15 minutes"}}, True),   # string, not a number
    ({"operation": "gap", "params": {"minutes": 900}}, True),            # outside 1..180
    ({"operation": "gap", "params": {"minutes": 0}}, True),              # zero is not a threshold
    ({"operation": "gap", "params": {"minutes": -30}}, True),            # negative
    ({"operation": "gap", "params": {"minutes": 45}}, False),            # the good case
])
def test_normalize_rejects_implausible_minutes(raw, expect_missing):
    out = planner.normalize(raw, "how far?")
    assert ("minutes" not in out["params"]) is expect_missing


def test_normalize_drops_invented_keys():
    """A model that invents a parameter must not be able to reach the analysis layer."""
    out = planner.normalize(
        {"operation": "shortlist",
         "params": {"limit": 3, "cost_model": "per_rupee", "budget_pkr": 5_000_000}},
        "which sites?")
    assert out["params"] == {"limit": 3}


def test_normalize_rejects_the_old_vocabulary():
    """
    The forked project's operations were find/coverage/distance_grid. A model primed on
    that vocabulary must fall back to something safe, not pass an unknown op through.
    """
    out = planner.normalize({"operation": "find", "params": {"target": "hospitals"}}, "show me schools")
    assert out["operation"] in planner.OPERATIONS
    assert "target" not in out["params"]


def test_normalize_coerces_free_text_intervention():
    out = planner.normalize(
        {"operation": "shortlist", "params": {"intervention": "build a school"}}, "where?")
    assert out["params"]["intervention"] in planner.INTERVENTIONS


def test_normalize_ignores_empty_district():
    out = planner.normalize({"operation": "summary", "params": {"district": "   "}}, "tell me")
    assert "district" not in out["params"]


def test_normalize_survives_garbage():
    for junk in (None, [], "a string", {"params": "not a dict"}, {}):
        out = planner.normalize(junk, "anything")
        assert out["operation"] in planner.OPERATIONS
        assert isinstance(out["params"], dict)


@pytest.mark.parametrize("question", [
    "where should we open learning centres?",
    "which sites should we prioritise?",
    "recommend somewhere to invest",
])
def test_prescriptive_questions_force_a_shortlist(question):
    """The model answers "gap" to these; the question is plainly asking for sites."""
    out = planner.normalize({"operation": "gap", "params": {}}, question)
    assert out["operation"] == "shortlist"


def test_off_topic_is_refused_by_the_keyword_router():
    assert planner.local_route("what's the weather tomorrow?")["operation"] == "unsupported"


@pytest.mark.parametrize("question,operation", [
    ("where are children more than 30 minutes from a school?", "gap"),
    ("which 5 sites should we open centres in?", "shortlist"),
    ("compare Malir and Tharparkar", "compare"),
    ("tell me about this district", "summary"),
])
def test_keyword_router_covers_the_question_set(question, operation):
    """The fallback must be usable on its own: it is what runs when Groq is rate limited."""
    assert planner.local_route(question)["operation"] == operation


def test_keyword_router_reads_minutes_and_cohort():
    out = planner.local_route("how many girls are beyond a 45 minute walk?")
    assert out["params"]["minutes"] == 45
    assert out["params"]["cohort"] == "girls"


def test_route_without_a_key_uses_keywords(monkeypatch):
    intent, how = planner.route("where should we open centres?")
    assert how.startswith("keywords")
    assert intent["operation"] == "shortlist"
