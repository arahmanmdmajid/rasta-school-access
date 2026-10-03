"""
The Verifier.

Two failure modes matter, and the second is the one that kills verifiers in practice:

  1. It must reject a number the analysis does not support.
  2. It must NOT reject the many legitimate ways prose restates a number. A verifier
     that fires on correct answers gets disabled, and then it protects nothing.

The acceptance tests below were written before the verifier itself, for that reason.
"""

import pytest

from rasta import verifier

FACTS = {
    "district": "Malir Karachi",
    "children_total": 797565,
    "children_beyond": 553751,
    "percent_beyond": 69,
    "threshold_min": 15,
}
TEXT = ("In Malir Karachi, about 553,751 of 797,565 school-age children (69%) live more "
        "than 15 minutes' walk from the nearest mapped school.")


# ----------------------------------------------------------------- rejection

def test_verifier_rejects_a_hallucinated_number():
    brief = "About 4,200 children in Malir Karachi are beyond a 15 minute walk."
    result = verifier.verify(brief, FACTS, TEXT)
    assert result["ok"] is False
    assert 4200 in result["unsupported"]


def test_verifier_rejects_an_invented_benchmark():
    """The classic LLM failure here: importing a standard nobody supplied."""
    brief = ("About 553,751 children are beyond a 15 minute walk, well above the "
             "international target of 20%.")
    assert verifier.verify(brief, FACTS, TEXT)["ok"] is False


def test_verifier_rejects_a_plausible_but_wrong_total():
    """Wrong by 13%: far outside anything rounding could explain."""
    brief = "In Malir Karachi, 553,751 of 900,000 children are beyond a 15 minute walk."
    result = verifier.verify(brief, FACTS, TEXT)
    assert result["ok"] is False
    assert 900000 in result["unsupported"]


def test_rounding_inside_the_tolerance_is_accepted_by_design():
    """
    "About 800,000" for 797,565 is off by 0.3%, and is how a person would actually write
    it. Rejecting that would make the verifier fire on good prose, which is how these
    things end up switched off. The boundary is documented here rather than left to be
    rediscovered.
    """
    brief = "About 800,000 school-age children live in the district."
    assert verifier.verify(brief, FACTS, TEXT)["ok"] is True


# ---------------------------------------------------------------- acceptance

def test_verifier_accepts_the_computed_text_itself():
    """If the deterministic text does not pass, nothing ever will."""
    assert verifier.verify(TEXT, FACTS, TEXT)["ok"] is True


@pytest.mark.parametrize("brief", [
    "553,751 children are beyond the threshold.",            # comma grouping
    "553751 children are beyond the threshold.",             # none
    "About 69% of children are too far from a school.",      # percent attached
    "About 69 % of children are too far from a school.",     # percent spaced
    "A 15-minute walk is the threshold.",                    # hyphenated unit
    "A 15 minute walk is the threshold.",                    # spaced unit
    "Roughly 554,000 children are affected.",                # honest rounding
    "Around 0.8 million children live in the district.",     # restated in millions
])
def test_verifier_accepts_reformatted_numbers(brief):
    result = verifier.verify(brief, FACTS, TEXT)
    assert result["ok"] is True, f"falsely rejected {result['unsupported']} in: {brief}"


def test_verifier_ignores_digits_inside_words():
    brief = "The COVID19 period is not relevant to this 69% figure."
    assert verifier.verify(brief, FACTS, TEXT)["ok"] is True


def test_verifier_counts_what_it_checked():
    result = verifier.verify("553,751 children, 69%, 15 minutes.", FACTS, TEXT)
    assert result["checked"] == 3


def test_verifier_passes_prose_with_no_numbers():
    assert verifier.verify("Most children here are too far from a school.", FACTS, TEXT)["ok"] is True
