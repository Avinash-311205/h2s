"""Cleaning: messy input in, deterministic clean record out."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.cleaning_service import CleaningService


def codes(cleaned) -> set[str]:
    return {issue.code for issue in cleaned.issues}


def test_cleans_whitespace_punctuation_and_nulls():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "  REQ-90001 ",
            "language": "Tamil",
            "category": "  road  ",
            "description": "  The   road\u00a0is\n\nbroken ",
            "severity": "4",
            "location": {"latitude": "12.9249", "longitude": "80.1000"},
            "created_at": "2026-09-13T10:30:00Z",
            "channel": None,
            "user_id": "null",
        }
    )

    assert cleaned.request_id == "REQ-90001"
    assert cleaned.language == "ta"
    assert cleaned.category_raw == "road"
    assert cleaned.description == "The road is broken"
    assert cleaned.severity == 4.0
    assert cleaned.latitude == 12.9249
    assert cleaned.longitude == 80.1
    assert cleaned.channel is None
    assert cleaned.user_id is None
    assert cleaned.created_at == datetime(2026, 9, 13, 10, 30, tzinfo=timezone.utc)
    assert cleaned.missing_fields == []


def test_treats_placeholder_values_as_missing():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "REQ-90002",
            "language": "en",
            "category": "unknown",
            "description": "N/A",
            "location": {"location_text": "  "},
        }
    )

    assert cleaned.category_raw is None
    assert cleaned.description is None
    assert cleaned.location_text is None
    assert set(cleaned.missing_fields) == {"category", "location", "created_at"}


def test_generates_placeholder_request_id_without_losing_data():
    service = CleaningService()

    cleaned = service.clean({"description": "Street light not working", "language": "en"})

    assert cleaned.request_id.startswith("REQ-UNASSIGNED")
    assert cleaned.request_id_valid is False
    assert "MISSING_REQUEST_ID" in codes(cleaned)
    assert cleaned.raw_payload


def test_flags_malformed_and_out_of_range_coordinates_but_keeps_them():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "REQ-90003",
            "category": "water",
            "latitude": 91.5,
            "longitude": "not-a-number",
            "description": "no water",
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert "INVALID_LATITUDE" in codes(cleaned)
    assert "MALFORMED_COORDINATE" in codes(cleaned)
    # The values are preserved for audit; they are simply unusable downstream.
    assert cleaned.latitude == 91.5
    assert cleaned.longitude is None
    assert cleaned.coordinates_valid is False


def test_flags_severity_outside_range():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "REQ-90004",
            "category": "road",
            "description": "pothole",
            "severity": 9,
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert "SEVERITY_OUT_OF_RANGE" in codes(cleaned)
    assert cleaned.severity == 9
    assert cleaned.severity_valid is False


def test_invalid_timestamp_is_flagged_not_dropped():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "REQ-90005",
            "category": "road",
            "description": "pothole",
            "created_at": "yesterday-ish",
        }
    )

    assert "INVALID_TIMESTAMP" in codes(cleaned)
    assert cleaned.created_at is None
    assert "created_at" in cleaned.missing_fields
    assert cleaned.raw_payload["created_at"] == "yesterday-ish"


def test_accepts_epoch_seconds_and_naive_strings():
    service = CleaningService()

    from_epoch = service.clean({"request_id": "REQ-90006", "description": "x", "created_at": 1_757_761_800})
    from_plain = service.clean({"request_id": "REQ-90007", "description": "x", "created_at": "2026-09-13 10:30:00"})

    assert from_epoch.created_at is not None
    assert from_epoch.created_at.tzinfo is not None
    assert from_plain.created_at == datetime(2026, 9, 13, 10, 30, tzinfo=timezone.utc)


def test_recognized_but_unsupported_language_is_a_warning():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "REQ-90009",
            "category": "road",
            "description": "Buraco na estrada",
            "language": "pt-BR",
            "location": {"latitude": -23.55, "longitude": -46.63},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert cleaned.language == "pt"
    assert "LANGUAGE_NOT_SUPPORTED" in codes(cleaned)
    assert cleaned.has_errors is False


def test_unknown_language_is_warning_not_error():
    service = CleaningService()

    cleaned = service.clean(
        {
            "request_id": "REQ-90008",
            "category": "road",
            "description": "x",
            "language": "klingon",
            "location": {"latitude": 12.9, "longitude": 80.1},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert "UNKNOWN_LANGUAGE" in codes(cleaned)
    assert cleaned.language_supported is False
    assert cleaned.has_errors is False
