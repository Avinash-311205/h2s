"""Validation: required fields, coordinate ranges, severity and category rules."""

from __future__ import annotations

from app.core.enums import DataQualityStatus
from app.services.cleaning_service import CleaningService
from app.services.geocoding_service import GeocodingService
from app.services.normalization_service import NormalizationService
from app.services.validation_service import ValidationService


cleaning = CleaningService()
normalization = NormalizationService()
geocoding = GeocodingService()
validation = ValidationService()


def run(payload: dict):
    cleaned = cleaning.clean(payload)
    normalized = normalization.normalize(
        description=cleaned.description,
        category=cleaned.category_raw,
        sub_category=cleaned.sub_category_raw,
        language=cleaned.language,
    )
    geocode, _ = geocoding.resolve(
        location_text=cleaned.location_text,
        latitude=cleaned.latitude,
        longitude=cleaned.longitude,
        district_hint=cleaned.district_hint,
        state_hint=cleaned.state_hint,
        country_hint=cleaned.country_hint,
    )
    return validation.validate(cleaned, normalized, geocode)


def test_complete_record_is_valid():
    result = run(
        {
            "request_id": "REQ-80001",
            "language": "en",
            "category": "road",
            "sub_category": "pothole",
            "description": "Road has multiple potholes",
            "severity": 4,
            "location": {"latitude": 12.9249, "longitude": 80.1},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert result.status == DataQualityStatus.VALID.value
    assert result.findings == []


def test_valid_coordinates_are_accepted():
    result = run(
        {
            "request_id": "REQ-80002",
            "category": "water",
            "sub_category": "drinking water",
            "description": "no drinking water",
            "severity": 3,
            "location": {"latitude": -33.9249, "longitude": 18.4241},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert "INVALID_COORDINATES" not in result.error_codes()


def test_invalid_coordinates_are_rejected():
    result = run(
        {
            "request_id": "REQ-80003",
            "category": "water",
            "sub_category": "drinking water",
            "description": "no drinking water",
            "severity": 3,
            "location": {"latitude": 91, "longitude": 200},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert result.status == DataQualityStatus.INVALID.value
    assert "INVALID_COORDINATES" in result.error_codes()


def test_severity_out_of_range_is_invalid():
    result = run(
        {
            "request_id": "REQ-80004",
            "category": "road",
            "sub_category": "pothole",
            "description": "pothole",
            "severity": 7,
            "location": {"latitude": 12.9, "longitude": 80.1},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert result.status == DataQualityStatus.INVALID.value
    assert "INVALID_SEVERITY" in result.error_codes()


def test_missing_timestamp_is_invalid():
    result = run(
        {
            "request_id": "REQ-80005",
            "category": "road",
            "sub_category": "pothole",
            "description": "pothole",
            "severity": 2,
            "location": {"latitude": 12.9, "longitude": 80.1},
        }
    )

    assert result.status == DataQualityStatus.INVALID.value
    assert "MISSING_TIMESTAMP" in result.error_codes()


def test_missing_location_is_invalid():
    result = run(
        {
            "request_id": "REQ-80006",
            "category": "road",
            "sub_category": "pothole",
            "description": "pothole",
            "severity": 2,
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert "MISSING_LOCATION" in result.error_codes()


def test_unknown_category_is_invalid():
    result = run(
        {
            "request_id": "REQ-80007",
            "description": "something unrelated",
            "created_at": "2026-09-13T10:30:00Z",
            "location": {"latitude": 12.9, "longitude": 80.1},
        }
    )

    assert result.status == DataQualityStatus.INVALID.value
    assert "INVALID_CATEGORY" in result.error_codes()


def test_missing_severity_only_needs_review():
    result = run(
        {
            "request_id": "REQ-80008",
            "language": "en",
            "category": "road",
            "sub_category": "pothole",
            "description": "Road has multiple potholes",
            "location": {"latitude": 12.9249, "longitude": 80.1},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert result.status == DataQualityStatus.NEEDS_REVIEW.value
    assert "MISSING_SEVERITY" in [finding.code for finding in result.warnings]


def test_unresolvable_location_only_needs_review():
    result = run(
        {
            "request_id": "REQ-80009",
            "language": "en",
            "category": "road",
            "sub_category": "pothole",
            "description": "Road has multiple potholes",
            "severity": 3,
            "location": {"location_text": "Somewhere unknown in the world"},
            "created_at": "2026-09-13T10:30:00Z",
        }
    )

    assert result.status == DataQualityStatus.NEEDS_REVIEW.value
    assert "LOCATION_UNRESOLVED" in [finding.code for finding in result.warnings]
