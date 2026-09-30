"""Category and sub-category classification.

Classification is lexicon-driven, so these tests pin down the behaviour that the
routing layer depends on: correct category, correct sub-category, and - just as
importantly - honest UNKNOWN output when there is no evidence.
"""

from __future__ import annotations

import pytest

from app.core.enums import Category, SeverityBand, SubCategory
from app.services.classification_service import classify_category, score_severity


@pytest.mark.parametrize(
    ("text", "category", "sub_category"),
    [
        ("No water supply in our colony since morning", Category.WATER, SubCategory.WATER_SUPPLY_DISRUPTION),
        ("Transformer is burning and sparking", Category.ELECTRICITY, SubCategory.TRANSFORMER_ISSUE),
        ("Street light is not working on the road", Category.ELECTRICITY, SubCategory.STREET_LIGHTING),
        ("Deep pothole caused an accident", Category.ROAD, SubCategory.DAMAGED_ROAD),
        ("Road is slightly damaged near my house", Category.ROAD, SubCategory.DAMAGED_ROAD),
        ("Garbage has not been collected for two weeks", Category.SANITATION, SubCategory.SOLID_WASTE),
        ("Sewage is overflowing on the street", Category.SANITATION, SubCategory.SEWAGE),
        ("There is no doctor in the health centre", Category.HEALTHCARE, SubCategory.HOSPITAL_ACCESS),
        ("Bus is not coming for three days", Category.TRANSPORT, SubCategory.PUBLIC_TRANSPORT),
        ("No internet in our village for two months", Category.DIGITAL_CONNECTIVITY, SubCategory.INTERNET_CONNECTIVITY),
        ("Mobile network is not working", Category.DIGITAL_CONNECTIVITY, SubCategory.MOBILE_NETWORK),
        ("School building wall is cracked", Category.EDUCATION, SubCategory.SCHOOL_INFRASTRUCTURE),
        ("Thieves stole my bag", Category.PUBLIC_SAFETY, SubCategory.STREET_CRIME),
    ],
)
def test_classifies_expected_category_and_subcategory(text, category, sub_category) -> None:
    prediction = classify_category(text)
    assert prediction.category == category.value
    assert prediction.sub_category == sub_category.value


@pytest.mark.parametrize("text", ["", "   ", "!!!"])
def test_blank_text_is_unknown_with_zero_confidence(text) -> None:
    prediction = classify_category(text)
    assert prediction.category == Category.UNKNOWN.value
    assert prediction.sub_category == SubCategory.UNCLASSIFIED.value
    assert prediction.confidence == 0.0


def test_unrelated_text_is_unknown_rather_than_forced() -> None:
    """No evidence must produce UNKNOWN, never a confident guess."""
    prediction = classify_category("I would like to say good morning to everyone")
    assert prediction.category == Category.UNKNOWN.value
    assert prediction.confidence == 0.0


def test_prediction_always_explains_itself() -> None:
    prediction = classify_category("No water supply in our colony")
    assert prediction.matched_keywords
    assert prediction.runner_up is None or prediction.runner_up != prediction.category


def test_confidence_is_a_probability_share() -> None:
    prediction = classify_category("No water supply for days in our colony")
    assert 0.0 < prediction.confidence <= 1.0


def test_runner_up_confidence_does_not_exceed_winner() -> None:
    prediction = classify_category("No water supply and no electricity in our colony")
    assert prediction.runner_up_confidence <= prediction.confidence


def test_specific_phrase_outvotes_generic_lemma() -> None:
    """Rich multi-word evidence must dominate a single generic word.

    "no water supply" is a decisive WATER phrase; "road" is only a weak ROAD
    lemma. Both contribute to their category, but the phrase-bearing one must
    win, and the shared evidence must show up as sub-certain confidence.
    """
    prediction = classify_category("No water supply near the road")
    assert prediction.category == Category.WATER.value
    assert 0.5 < prediction.confidence < 1.0
    assert prediction.runner_up == Category.ROAD.value


def test_severity_is_bounded_and_monotonic() -> None:
    mild = score_severity("Street light is not working")
    severe = score_severity("Transformer is burning and sparking, immediate danger")
    assert 1 <= mild.severity <= 5
    assert 1 <= severe.severity <= 5
    assert severe.severity > mild.severity


def test_duration_escalates_severity() -> None:
    fresh = score_severity("Pothole on the road")
    persistent = score_severity("Pothole on the road", None, max_duration_days=30)
    assert persistent.severity > fresh.severity


def test_mitigator_terms_reduce_severity() -> None:
    """A citizen saying the problem is resolved must not page an officer."""
    plain = score_severity("There is a water problem")
    mitigated = score_severity("There is a water problem but it is already fixed")
    assert mitigated.severity < plain.severity
    assert mitigated.severity <= 2


def test_mitigator_beats_stronger_harm_language() -> None:
    """Mitigators cap severity even when a harm phrase is also present."""
    severity = score_severity("There was no water supply for days but it is resolved now")
    assert severity.severity <= 2


def test_personal_health_is_routed_high() -> None:
    severity = score_severity("My mother is ill and there is no doctor")
    assert severity.severity >= 4


def test_severity_band_matches_numeric_severity() -> None:
    severity = score_severity("Transformer is burning and sparking")
    assert severity.band in {e.value for e in SeverityBand}
    assert severity.base_severity <= severity.severity <= 5
    assert severity.confidence > 0.0


def test_severity_explains_itself() -> None:
    severity = score_severity("Transformer is burning and sparking")
    assert severity.matched_keywords