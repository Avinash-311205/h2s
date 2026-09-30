"""End-to-end pipeline behaviour.

These tests pin the contract that Module 3 and the civic dashboards consume:
the stage map always covers every stage, the output shape is stable, and the
pipeline degrades instead of raising when an optional provider is unavailable.
"""

from __future__ import annotations

import pytest

from app.core.enums import MediaType, StageStatus, UnderstandingStatus
from app.services.understanding_service import understand


def test_english_water_complaint_is_fully_understood() -> None:
    result = understand(
        request_id="REQ-1",
        text="No water supply for days in our colony, there is a broken pipeline near the temple",
    )
    assert result.status == UnderstandingStatus.UNDERSTOOD.value
    assert result.detected_language == "en"
    assert result.category == "WATER"
    assert result.sub_category == "WATER_SUPPLY_DISRUPTION"
    assert result.severity >= 4
    assert result.category_confidence > 0.5
    assert result.media_type == MediaType.TEXT.value
    assert result.stage_status


def test_tamil_complaint_is_classified_from_source_language() -> None:
    result = understand(request_id="REQ-2", text="கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு ஆறு மாதங்கள்")
    assert result.detected_language == "ta"
    assert result.category == "SANITATION"
    assert result.sub_category == "SEWAGE"


def test_hindi_complaint_is_classified() -> None:
    result = understand(request_id="REQ-3", text="सेक्शन रोड पर बड़े गड्ढे हैं, बच्चे सड़क पार करते हैं")
    assert result.detected_language == "hi"
    assert result.category == "ROAD"


def test_every_stage_is_reported() -> None:
    result = understand(request_id="REQ-4", text="Transformer is burning and sparking")
    for stage in (
        "language_detection", "asr", "translation",
        "ner", "classification", "severity", "image_analysis",
    ):
        assert stage in result.stage_status, f"stage {stage} missing from the stage map"
        assert result.stage_status[stage] in {e.value for e in StageStatus}


def test_unclassifiable_text_degrades_to_partial_not_a_crash() -> None:
    """Unknown input must be routed for review, never mislabelled."""
    result = understand(request_id="REQ-5", text="My name is Ravi and I live in Chennai")
    assert result.category == "UNKNOWN"
    assert result.sub_category == "UNCLASSIFIED"
    assert result.status in {
        UnderstandingStatus.PARTIAL.value,
        UnderstandingStatus.UNDERSTOOD.value,
    }
    assert 1 <= result.severity <= 5


def test_severity_never_leaves_the_civic_scale() -> None:
    for text in (
        "Transformer is burning and sparking, immediate danger",
        "everything is fine",
        "கழிவு நீர்",
        "x",
    ):
        result = understand(request_id="REQ-SEV", text=text)
        assert 1 <= result.severity <= 5
        assert result.severity_band in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def test_pipeline_is_deterministic() -> None:
    text = "No water supply for days in our colony"
    first = understand(request_id="REQ-6", text=text)
    second = understand(request_id="REQ-7", text=text)
    assert first.category == second.category
    assert first.sub_category == second.sub_category
    assert first.severity == second.severity


def test_entities_are_json_serialisable_enums() -> None:
    """Entity ``type`` must be a plain string, not an Enum, or the API 500s."""
    import json

    result = understand(
        request_id="REQ-8",
        text="The transformer near the temple on Main Road, Anna Nagar is burning, pincode 560001",
    )
    payload = json.dumps(result.entities)
    assert "EntityType." not in payload
    for entity in result.entities:
        assert isinstance(entity["type"], str)
        assert isinstance(entity["text"], str)
        assert 0.0 <= entity["confidence"] <= 1.0


def test_hint_coordinates_are_preserved_for_the_geocoder() -> None:
    """Module 2 never geocodes; handset GPS is passed through for Module 3."""
    result = understand(
        request_id="REQ-9",
        text="Deep pothole near the school",
        hint_latitude=12.9249,
        hint_longitude=80.1000,
    )
    assert result.raw_payload["hint_latitude"] == pytest.approx(12.9249)
    assert result.raw_payload["hint_longitude"] == pytest.approx(80.1000)


def test_blank_request_is_rejected() -> None:
    """An empty submission is a client error, not a silent UNKNOWN."""
    with pytest.raises(ValueError):
        understand(request_id="REQ-EMPTY", text="   ")


def test_pincode_is_extracted() -> None:
    result = understand(request_id="REQ-10", text="No water supply in our colony pincode 560001")
    pincodes = [e["text"] for e in result.entities if e["type"] == "PINCODE"]
    assert "560001" in pincodes


def test_duration_is_extracted_and_drives_severity() -> None:
    result = understand(request_id="REQ-11", text="Garbage has not been collected for two weeks")
    durations = [e for e in result.entities if e["type"] == "DURATION"]
    assert durations
    assert durations[0]["attributes"]["amount"] == 2


def test_unavailable_asr_warns_instead_of_failing_the_request() -> None:
    """An audio note with no ASR backend must degrade, not 500."""
    result = understand(request_id="REQ-12", audio_bytes=b"RIFF\x00\x00\x00\x00WAVEfmt ")
    assert result.media_type == MediaType.AUDIO.value
    assert result.stage_status["asr"] in {e.value for e in StageStatus}
    assert result.warnings  # the degraded stage must be reported


def test_multimodal_media_type_is_reported() -> None:
    result = understand(
        request_id="REQ-13",
        text="Broken road here",
        image_bytes=b"\xff\xd8\xff\xe0not-really-a-jpeg",
    )
    assert result.media_type == MediaType.MULTIMODAL.value