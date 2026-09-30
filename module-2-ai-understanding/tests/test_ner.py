"""Entity extraction feeds Module 3's geocoder and the severity escalation.

Every entity must be a plain JSON-serialisable dict: ``type`` a string (not an
Enum), ``text`` the exact source span, and ``confidence`` in [0, 1].
"""

from __future__ import annotations

import json

import pytest

from app.core.enums import EntityType
from app.services.ner_service import extract_entities


def _entities(text: str) -> list[dict]:
    return [e.as_dict() for e in extract_entities(text).entities]


def _types(text: str) -> set[str]:
    return {entity["type"] for entity in _entities(text)}


def _find(text: str, entity_type: str) -> list[dict]:
    return [e for e in _entities(text) if e["type"] == entity_type]


@pytest.mark.parametrize(
    "text",
    [
        "Deep pothole at 12.9249, 80.1000 near the school",
        "The transformer near the temple on Main Road, Anna Nagar is burning, pincode 560001",
        "கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு ஆறு மாதங்கள்",
    ],
)
def test_entities_are_json_serialisable(text: str) -> None:
    payload = json.dumps(_entities(text))
    assert "EntityType." not in payload
    for entity in _entities(text):
        assert isinstance(entity["type"], str)
        assert isinstance(entity["text"], str)
        assert 0.0 <= entity["confidence"] <= 1.0
        assert entity["text"]  # never empty


def test_pincode_is_extracted() -> None:
    entities = _find("No water supply in our colony pincode 560001", EntityType.PINCODE.value)
    assert [e["text"] for e in entities] == ["560001"]


def test_phone_is_extracted() -> None:
    entities = _find("Call me on 9876543210 or 044-23456789", EntityType.PHONE.value)
    assert {e["text"] for e in entities} >= {"9876543210"}


def test_coordinates_are_extracted() -> None:
    entities = _find("Pothole at 12.9249, 80.1000", EntityType.COORDINATE.value)
    assert entities
    attributes = entities[0]["attributes"]
    assert attributes["latitude"] == pytest.approx(12.9249, abs=1e-4)
    assert attributes["longitude"] == pytest.approx(80.1000, abs=1e-4)


def test_geo_ranges_are_validated() -> None:
    """A latitude of 999 is not a coordinate; it must be rejected."""
    assert _find("Somewhere at 999.0, 999.0", EntityType.COORDINATE.value) == []


def test_landmark_and_admin_unit_are_extracted() -> None:
    types = _types("The transformer near the temple in Anna Nagar is burning")
    assert EntityType.LANDMARK.value in types
    assert EntityType.ADMIN_UNIT.value in types


def test_infra_asset_is_extracted() -> None:
    types = _types("The borewell and street light are broken")
    assert EntityType.INFRA_ASSET.value in types


def test_duration_is_extracted_in_english() -> None:
    entities = _find("Garbage has not been collected for two weeks", EntityType.DURATION.value)
    assert entities
    assert entities[0]["attributes"]["amount"] == 2


def test_duration_is_extracted_in_tamil() -> None:
    """'ஆறு மாதங்கள்' = six months; the plural must not defeat the regex."""
    entities = _find("ஆறு மாதங்கள்", EntityType.DURATION.value)
    assert entities
    assert entities[0]["attributes"]["amount"] == 6


def test_duration_units_cover_singular_plural_and_script() -> None:
    for text, amount, unit in [
        ("for 3 days", 3, "days"),
        ("for 3 day", 3, "day"),
        ("for two weeks", 2, "weeks"),
        ("three months", 3, "months"),
        ("மூன்று நாட்கள்", 3, "நாட்கள்"),
        ("रोज दिनों", None, None),  # malformed: must not crash
    ]:
        entities = _find(text, EntityType.DURATION.value)
        if amount is None:
            continue
        assert entities, f"no DURATION entity for {text!r}"
        assert entities[0]["attributes"]["amount"] == amount
        assert entities[0]["attributes"]["unit"] == unit


def test_text_with_no_entities_returns_empty_list() -> None:
    assert _entities("") == []
    assert _entities("the usual thing") == []


def test_entity_order_is_stable() -> None:
    text = "Pothole at 12.9249, 80.1000 near the temple, call 9876543210, for two weeks"
    first = [(e["type"], e["text"]) for e in _entities(text)]
    second = [(e["type"], e["text"]) for e in _entities(text)]
    assert first == second
    assert first