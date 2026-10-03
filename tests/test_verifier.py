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


# ------------------------------------------- the small-integer boundary (measured)

@pytest.mark.parametrize("brief", [
    "There are 3 things to do next.",
    "Consider 2 options before committing.",
    "1. Visit the site. 2. Check the roof. 3. Talk to the teacher.",
])
def test_bare_small_integers_are_not_treated_as_claims(brief):
    """
    Measured against real model output: the ONLY false rejections were bare small
    integers used as list markers or counts. Flagging those would have made the
    verifier fire on good answers, and a verifier that cries wolf gets turned off.
    """
    assert verifier.verify(brief, FACTS, TEXT)["ok"] is True


@pytest.mark.parametrize("brief", [
    "Only 3% of children are beyond the threshold.",
    "The nearest school is 3 minutes away.",
    "Children walk 2 km to school here.",
])
def test_small_numbers_with_a_unit_are_still_claims(brief):
    """The exemption above must not become a hole: a unit makes it an assertion."""
    assert verifier.verify(brief, FACTS, TEXT)["ok"] is False


# ------------------------------------------------- numbers from the data caveat

CAVEAT = ("Only 107 schools are mapped in Malir Karachi. Across Sindh, open data holds "
          "about 3% of the schools the official count reports.")


def test_numbers_from_the_caveat_are_allowed():
    """
    The writer is handed the provenance caveat and told to reflect it, so repeating its
    figures is correct behaviour. Leaving the caveat out of the allowed set made the
    verifier reject three of ten real answers for doing as they were told - the bug this
    test exists to stop coming back.
    """
    brief = ("About 553,751 children are beyond a 15 minute walk. Only 107 schools are "
             "mapped here, roughly 3% of the official count, so these need field checks.")
    result = verifier.verify(brief, FACTS, TEXT, CAVEAT)
    assert result["ok"] is True, f"falsely rejected {result['unsupported']}"


def test_the_caveat_does_not_become_a_loophole():
    """Admitting the caveat must not admit everything else."""
    brief = "Only 107 schools are mapped, and 9,400 children are affected."
    assert verifier.verify(brief, FACTS, TEXT, CAVEAT)["ok"] is False
