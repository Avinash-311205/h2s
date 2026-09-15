"""Data quality scoring.

Produces a single ``0.0 - 1.0`` score plus a status so that downstream modules
(and the dashboard) can weight records by trustworthiness.

The scoring logic is modular: each factor is computed independently and
combined through configurable weights, so the formula can be tuned (or replaced
by a learned model) without touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.core.enums import ALLOWED_CATEGORIES, DataQualityStatus
from app.core.geo import valid_coordinates
from app.core.logging import get_logger
from app.services.cleaning_service import CleanedRecord
from app.services.geocoding_service import GeocodeResult
from app.services.normalization_service import NormalizationResult
from app.services.validation_service import ValidationResult

logger = get_logger(__name__)

DEFAULT_WEIGHTS: dict[str, float] = {
    "required_fields": 0.22,
    "location_valid": 0.14,
    "category_valid": 0.14,
    "timestamp_valid": 0.10,
    "geocode_resolved": 0.14,
    "severity_valid": 0.10,
    "normalization_confidence": 0.16,
}

VALID_THRESHOLD = 0.85


@dataclass
class QualityScore:
    score: float
    status: str
    factors: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_quality_score": self.score,
            "data_quality_status": self.status,
            "factors": self.factors,
        }


class QualityService:
    """Weighted, explainable quality score."""

    def __init__(self, weights: Optional[dict[str, float]] = None):
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("Quality weights must sum to a positive value")
        # Normalize so callers may pass arbitrary weights.
        self.weights = {key: value / total for key, value in self.weights.items()}

    # ------------------------------------------------------------------
    def score(
        self,
        cleaned: CleanedRecord,
        normalized: NormalizationResult,
        geocode: GeocodeResult,
        validation: ValidationResult,
    ) -> QualityScore:
        factors = {
            "required_fields": self._required_fields_factor(cleaned),
            "location_valid": self._location_factor(cleaned, geocode),
            "category_valid": self._category_factor(normalized),
            "timestamp_valid": self._timestamp_factor(cleaned),
            "geocode_resolved": self._geocode_factor(geocode),
            "severity_valid": self._severity_factor(cleaned),
            "normalization_confidence": self._clamp(normalized.confidence),
        }

        score = sum(self.weights[name] * value for name, value in factors.items())
        score = round(self._clamp(score), 3)

        return QualityScore(
            score=score,
            status=self._status(score, validation),
            factors={name: round(value, 3) for name, value in factors.items()},
            weights={name: round(value, 4) for name, value in self.weights.items()},
        )

    # ------------------------------------------------------------------
    def _status(self, score: float, validation: ValidationResult) -> str:
        if validation.status == DataQualityStatus.INVALID.value:
            return DataQualityStatus.INVALID.value
        if validation.status == DataQualityStatus.NEEDS_REVIEW.value:
            return DataQualityStatus.NEEDS_REVIEW.value
        return DataQualityStatus.VALID.value if score >= VALID_THRESHOLD else DataQualityStatus.NEEDS_REVIEW.value

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _required_fields_factor(self, cleaned: CleanedRecord) -> float:
        checks: list[Callable[[], bool]] = [
            lambda: cleaned.request_id_valid,
            lambda: bool(cleaned.category_raw) or bool(cleaned.description),
            lambda: cleaned.location_present,
            lambda: cleaned.created_at is not None,
        ]
        present = sum(1 for check in checks if check())
        return self._clamp(present / len(checks))

    def _location_factor(self, cleaned: CleanedRecord, geocode: GeocodeResult) -> float:
        coordinates_ok = valid_coordinates(cleaned.latitude, cleaned.longitude)
        admin_ok = bool(geocode.district or geocode.state)

        if coordinates_ok and admin_ok:
            return 1.0
        if coordinates_ok:
            return 0.75
        if admin_ok:
            return 0.6
        if cleaned.location_text:
            return 0.35
        return 0.0

    def _category_factor(self, normalized: NormalizationResult) -> float:
        if normalized.category in ALLOWED_CATEGORIES:
            return 1.0 if normalized.sub_category_known else 0.75
        return 0.1

    def _timestamp_factor(self, cleaned: CleanedRecord) -> float:
        if cleaned.created_at is None:
            return 0.0
        now = datetime.now(timezone.utc)
        if cleaned.created_at > now + timedelta(days=1):
            return 0.4
        if cleaned.created_at.year < 1970:
            return 0.2
        return 1.0

    @staticmethod
    def _geocode_factor(geocode: GeocodeResult) -> float:
        if geocode.location_status == "RESOLVED":
            return 1.0
        if geocode.location_status == "PARTIAL":
            return 0.6
        return 0.0

    @staticmethod
    def _severity_factor(cleaned: CleanedRecord) -> float:
        if cleaned.severity is None:
            return 0.4
        return 1.0 if cleaned.severity_valid else 0.0
