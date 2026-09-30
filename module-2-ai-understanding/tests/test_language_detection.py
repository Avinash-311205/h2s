"""Language detection must work without any model download.

Detection is script-frequency based so it is deterministic, dependency-free and
instant - important because it runs on every single request before the rest of
the pipeline and must never fail the request.
"""

from __future__ import annotations

import pytest

from app.services.language_service import canonical_language_code, detect_language


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No water supply in our colony since morning", "en"),
        ("There is no electricity in the village", "en"),
        ("இந்த சாலையில் நிறைய பள்ளங்கள் உள்ளன", "ta"),
        ("தண்ணீர் இல்லை", "ta"),
        ("गंदा पानी नाली में जा रहा है", "hi"),
        ("सड़क में बड़े गड्ढे हैं", "hi"),
        ("రోడ్డులో పెద్ద గుంతలు ఉన్నాయి", "te"),
        ("చెరువు ఎప్పుడూ తీయించారు", "te"),
    ],
)
def test_detects_supported_languages(text: str, expected: str) -> None:
    detected = detect_language(text)
    assert detected["language"] == expected
    assert detected["confidence"] > 0.0


def test_detect_language_is_deterministic() -> None:
    text = "கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு"
    first = detect_language(text)
    second = detect_language(text)
    assert first == second


def test_empty_text_is_reported_undetermined() -> None:
    """Empty input is reported honestly rather than guessed at."""
    detected = detect_language("")
    assert detected["language"] == "und"
    assert detected["confidence"] == 0.0
    assert detected["method"] == "empty_input"


def test_punctuation_only_has_no_confident_language() -> None:
    detected = detect_language("!!! ??? ...")
    assert detected["language"] == "und"
    assert detected["confidence"] < 0.5


def test_mixed_script_prefers_dominant_script() -> None:
    """A stray latin token (a pincode, a name) must not flip the language."""
    detected = detect_language("சாலை பழுது pincode 560001")
    assert detected["language"] == "ta"


def test_detection_exposes_audit_fields() -> None:
    detected = detect_language("No water supply")
    assert detected["method"] == "latin_english_markers"
    assert detected["script"] == "latn"


def test_romanised_indic_is_distinguished_from_english() -> None:
    detected = detect_language("pani nahi aa raha hai gaon me")
    assert detected["language"] == "romanised"
    assert detected["confidence"] < 0.9


def test_canonical_language_code_normalises_aliases() -> None:
    assert canonical_language_code("en") == "en"
    assert canonical_language_code("EN") == "en"
    assert canonical_language_code("EN-gb") == "en"
    assert canonical_language_code("zz") == "und"
    assert canonical_language_code(None) == "und"