"""Normalization: free text -> canonical category / sub-category."""

from __future__ import annotations

from app.services.normalization_service import (
    NormalizationDictionary,
    NormalizationService,
    get_dictionary,
    reload_dictionary,
)


service = NormalizationService()


def test_pothole_maps_to_damaged_road():
    result = service.normalize(description="pothole road", category="road", sub_category="pothole", language="en")

    assert (result.category, result.sub_category) == ("ROAD", "DAMAGED_ROAD")
    assert result.confidence >= 0.9
    assert result.match_source == "explicit"


def test_description_only_infers_category_and_sub_category():
    result = service.normalize(description="The road is broken and full of potholes", language="en")

    assert (result.category, result.sub_category) == ("ROAD", "DAMAGED_ROAD")
    assert result.matched_keywords


def test_drinking_water_phrasings_all_normalize_the_same():
    phrasings = [
        "no drinking water",
        "water supply problem",
        "tap water unavailable",
        "There is no water supply in our street",
    ]

    for phrasing in phrasings:
        result = service.normalize(description=phrasing, language="en")
        assert (result.category, result.sub_category) == ("WATER", "DRINKING_WATER"), phrasing


def test_tamil_sample_description_is_normalized():
    result = service.normalize(
        description="இந்த சாலையில் நிறைய பள்ளங்கள் உள்ளன",
        category="road",
        sub_category="pothole",
        language="ta",
    )

    assert (result.category, result.sub_category) == ("ROAD", "DAMAGED_ROAD")


def test_multilingual_keyword_inference_without_hints():
    cases = {
        "hi": "सड़क टूटी है और गड्ढे हैं",
        "te": "రోడ్డు గుంతలు ఉన్నాయి",
        "bn": "রাস্তায় গর্ত আছে",
        "kn": "ರಸ್ತೆಯಲ್ಲಿ ಗುಂಡಿಗಳಿವೆ",
        "ml": "റോഡിൽ കുഴികൾ ഉണ്ട്",
        "ta": "சாலையில் பள்ளங்கள் உள்ளன",
    }

    for language, description in cases.items():
        result = service.normalize(description=description, language=language)
        assert (result.category, result.sub_category) == ("ROAD", "DAMAGED_ROAD"), language


def test_bridge_phrasings_normalize_to_bridge_damage():
    phrasings = ["Bridge damaged", "Bridge is broken", "Dangerous bridge", "Bridge needs repair"]

    for phrasing in phrasings:
        result = service.normalize(description=phrasing, category="transport", language="en")
        assert (result.category, result.sub_category) == ("TRANSPORT", "BRIDGE_DAMAGE"), phrasing


def test_explicit_category_is_authoritative_but_mismatch_is_reported():
    result = service.normalize(description="potholes everywhere", category="water", sub_category="pothole")

    assert result.category == "WATER"
    assert result.category_mismatch is True
    assert result.conflicting_sub_category == "pothole"


def test_unknown_description_falls_back_safely():
    result = service.normalize(description="something completely unrelated", language="en")

    assert result.category == "UNKNOWN"
    assert result.sub_category == "UNCLASSIFIED"
    assert result.confidence == 0.0
    assert result.match_source == "fallback"


def test_dictionary_is_data_driven_and_extensible():
    """A new language can be added purely through data."""
    data = {
        "version": "test",
        "languages": ["en", "xx"],
        "categories": {
            "ROAD": {
                "aliases": {"en": ["road"], "xx": ["zorblat"]},
                "sub_categories": {
                    "DAMAGED_ROAD": {
                        "aliases": ["pothole"],
                        "keywords": {"en": ["pothole"], "xx": ["glorb"]},
                    }
                },
            }
        },
    }

    dictionary = NormalizationDictionary(data)
    local_service = NormalizationService(dictionary)

    result = local_service.normalize(description="glorb everywhere", language="xx")

    assert (result.category, result.sub_category) == ("ROAD", "DAMAGED_ROAD")
    assert "glorb" in result.matched_keywords


def test_shipped_dictionary_is_loaded_and_multilingual():
    dictionary = get_dictionary()
    summary = dictionary.summary()

    assert summary["categories"] >= 10
    assert summary["keywords"] > 100
    for language in ("en", "ta", "hi", "te", "bn", "kn", "ml"):
        assert language in summary["languages"]


def test_reload_dictionary_returns_cached_instance_again():
    first = get_dictionary()
    reloaded = reload_dictionary()

    assert reloaded is not first
    assert reloaded.summary()["version"] == first.summary()["version"]
