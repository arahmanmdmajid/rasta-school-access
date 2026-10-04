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


# ------------------------------------------------- questions about the tool itself

@pytest.mark.parametrize("question,topic", [
    ("what does the info on the map shows?", "overview"),
    ("what am I looking at?", "overview"),
    ("what is Rasta?", "overview"),
    ("how do I use this?", "overview"),
    ("what do the colours mean?", "colours"),
    ("what are the red areas?", "colours"),
    ("how do you calculate the walk time?", "method"),
    ("what does 15 minutes mean?", "method"),
    ("where does the data come from?", "data"),
    ("how accurate is this?", "limits"),
    ("what are the limitations?", "limits"),
    ("can I trust these numbers?", "limits"),
    ("is this real data?", "limits"),
    ("which districts do you have?", "coverage"),
    ("does it cover Punjab?", "coverage"),
    ("why only Sindh?", "coverage"),
    ("how does the AI work?", "ai"),
    ("could this work for clinics?", "scope"),
])
def test_questions_about_the_tool_are_answered_not_refused(question, topic):
    """
    Twelve of these seventeen were refused before. They are the first things anyone new
    asks - a judge most of all - and "I can only answer questions about school access"
    makes the assistant look broken rather than careful.
    """
    out = planner.local_route(question, ["Malir Karachi", "Dadu"])
    assert out["operation"] == "explain", f"refused: {question}"
    assert out["params"]["topic"] == topic


def test_an_explain_always_carries_a_topic():
    """analyst.explain falls back to overview, but the planner should be explicit."""
    out = planner.normalize({"operation": "explain", "params": {}}, "what is this?")
    assert out["params"]["topic"] in planner.TOPICS


def test_a_tool_question_beats_a_stray_keyword():
    """
    "how do you calculate the walk time" contains "walk", which used to route it to a
    district summary - an answer to a question nobody asked.
    """
    assert planner.local_route("how do you calculate the walk time?")["params"]["topic"] == "method"


def test_asking_about_a_district_still_wins_over_explain():
    out = planner.local_route("tell me about Dadu", ["Dadu", "Malir Karachi"])
    assert out["operation"] == "summary"
    assert out["params"]["district"] == "Dadu"


def test_still_refuses_what_it_should():
    for junk in ("what's the weather tomorrow?", "tell me a joke", "who won the cricket?"):
        assert planner.local_route(junk)["operation"] == "unsupported", junk


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
