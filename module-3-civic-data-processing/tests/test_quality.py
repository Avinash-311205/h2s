"""Data quality scoring: complete records score high, incomplete ones score low."""

from __future__ import annotations

from app.core.enums import DataQualityStatus
from app.services.cleaning_service import CleaningService
from app.services.geocoding_service import GeocodingService
from app.services.normalization_service import NormalizationService
from app.services.quality_service import DEFAULT_WEIGHTS, QualityService
from app.services.validation_service import ValidationService


cleaning = CleaningService()
normalization = NormalizationService()
geocoding = GeocodingService()
validation = ValidationService()
quality = QualityService()

COMPLETE = {
    "request_id": "REQ-70001",
    "language": "en",
    "category": "road",
    "sub_category": "pothole",
    "description": "Road has multiple potholes",
    "severity": 4,
    "location": {"latitude": 12.9249, "longitude": 80.1},
    "created_at": "2026-09-13T10:30:00Z",
}


def score_for(payload: dict, service: QualityService = quality):
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
    validation_result = validation.validate(cleaned, normalized, geocode)
    return service.score(cleaned, normalized, geocode, validation_result)


def test_complete_record_scores_high():
    result = score_for(COMPLETE)

    assert result.score >= 0.9
    assert result.status == DataQualityStatus.VALID.value


def test_incomplete_record_scores_lower():
    incomplete = {"request_id": "REQ-70002", "description": "something is wrong somewhere"}

    complete = score_for(COMPLETE)
    partial = score_for(incomplete)

    assert partial.score < complete.score
    assert partial.status == DataQualityStatus.INVALID.value


def test_score_is_always_within_bounds():
    for payload in (
        COMPLETE,
        {"request_id": "REQ-70003", "description": "x"},
        {"request_id": "REQ-70004"},
    ):
        result = score_for(payload)
        assert 0.0 <= result.score <= 1.0


def test_score_reports_explainable_factors():
    result = score_for(COMPLETE)

    assert set(result.factors) == set(DEFAULT_WEIGHTS)
    assert all(0.0 <= value <= 1.0 for value in result.factors.values())
    assert result.factors["geocode_resolved"] == 1.0
    assert result.factors["category_valid"] == 1.0


def test_unresolved_location_lowers_the_score():
    resolved = score_for(COMPLETE)
    unresolved = score_for(
        {**COMPLETE, "request_id": "REQ-70005", "location": {"location_text": "Nowhereville"}}
    )

    assert unresolved.score < resolved.score
    assert unresolved.factors["geocode_resolved"] == 0.0


def test_weights_are_configurable_and_normalized():
    equal_weights = {name: 1.0 for name in DEFAULT_WEIGHTS}
    custom = QualityService(equal_weights)

    assert abs(sum(custom.weights.values()) - 1.0) < 1e-9
    assert abs(custom.weights["required_fields"] - (1.0 / len(DEFAULT_WEIGHTS))) < 1e-9

    result = score_for(COMPLETE, service=custom)
    assert 0.0 <= result.score <= 1.0
