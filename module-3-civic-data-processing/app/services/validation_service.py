"""Validation service.

Enforces the platform's data contract *after* cleaning, normalization and
geocoding. Nothing is discarded: every check produces a finding, and the
aggregate outcome becomes the record's ``data_quality_status``:

* ``VALID``         - no errors, no warnings
* ``NEEDS_REVIEW``  - usable but incomplete/imprecise
* ``INVALID``       - a hard rule was violated (still stored, never deleted)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.enums import ALLOWED_CATEGORIES, DataQualityStatus, SeverityFinding
from app.core.geo import valid_coordinates
from app.core.logging import get_logger
from app.services.cleaning_service import CleanedRecord
from app.services.geocoding_service import GeocodeResult
from app.services.normalization_service import NormalizationResult

logger = get_logger(__name__)

MAX_FUTURE_SKEW = timedelta(days=1)
MIN_REASONABLE_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class ValidationFinding:
    code: str
    message: str
    severity: str = SeverityFinding.ERROR.value
    field: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": "validation",
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "field": self.field,
        }


@dataclass
class ValidationResult:
    status: str = DataQualityStatus.NEEDS_REVIEW.value
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == SeverityFinding.ERROR.value]

    @property
    def warnings(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == SeverityFinding.WARNING.value]

    @property
    def is_valid(self) -> bool:
        return self.status == DataQualityStatus.VALID.value

    def error_codes(self) -> list[str]:
        return [f.code for f in self.errors]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class ValidationService:
    """Applies the civic data contract to a fully processed record."""

    def __init__(self, allowed_categories: tuple[str, ...] = ALLOWED_CATEGORIES):
        self.allowed_categories = allowed_categories

    def validate(
        self,
        cleaned: CleanedRecord,
        normalized: NormalizationResult,
        geocode: GeocodeResult,
    ) -> ValidationResult:
        findings: list[ValidationFinding] = []

        self._validate_required_fields(cleaned, findings)
        self._validate_category(normalized, findings)
        self._validate_location(cleaned, geocode, findings)
        self._validate_timestamp(cleaned, findings)
        self._validate_severity(cleaned, findings)
        self._validate_description(cleaned, findings)
        self._validate_language(cleaned, findings)
        self._validate_normalization(normalized, findings)

        has_errors = any(f.severity == SeverityFinding.ERROR.value for f in findings)
        has_warnings = any(f.severity == SeverityFinding.WARNING.value for f in findings)

        if has_errors:
            status = DataQualityStatus.INVALID.value
        elif has_warnings:
            status = DataQualityStatus.NEEDS_REVIEW.value
        else:
            status = DataQualityStatus.VALID.value

        return ValidationResult(status=status, findings=findings)

    # ------------------------------------------------------------------
    def _validate_required_fields(self, cleaned: CleanedRecord, findings: list[ValidationFinding]) -> None:
        if not cleaned.request_id_valid:
            findings.append(
                ValidationFinding(
                    code="INVALID_REQUEST_ID",
                    message="request_id is missing or malformed; the record cannot be traced back to Module 1.",
                    field="request_id",
                )
            )
        if not cleaned.category_raw and not cleaned.description:
            findings.append(
                ValidationFinding(
                    code="MISSING_CATEGORY",
                    message="no category could be derived from the payload.",
                    field="category",
                )
            )

    def _validate_category(self, normalized: NormalizationResult, findings: list[ValidationFinding]) -> None:
        if normalized.category not in self.allowed_categories:
            findings.append(
                ValidationFinding(
                    code="INVALID_CATEGORY",
                    message=(
                        f"category '{normalized.category}' is not in the allowed list "
                        f"({', '.join(self.allowed_categories)})."
                    ),
                    field="category",
                )
            )
        if not normalized.sub_category_known:
            findings.append(
                ValidationFinding(
                    code="MISSING_SUB_CATEGORY",
                    message="sub_category could not be normalized; the record needs manual review.",
                    severity=SeverityFinding.WARNING.value,
                    field="sub_category",
                )
            )
        if normalized.category_mismatch:
            findings.append(
                ValidationFinding(
                    code="CATEGORY_SUBCATEGORY_MISMATCH",
                    message=(
                        f"sub_category '{normalized.conflicting_sub_category}' does not belong to "
                        f"category '{normalized.category}'; the category was kept and the sub-category re-derived."
                    ),
                    severity=SeverityFinding.WARNING.value,
                    field="sub_category",
                )
            )

    def _validate_location(
        self,
        cleaned: CleanedRecord,
        geocode: GeocodeResult,
        findings: list[ValidationFinding],
    ) -> None:
        if not cleaned.location_present:
            findings.append(
                ValidationFinding(
                    code="MISSING_LOCATION",
                    message="neither coordinates nor a location description were provided.",
                    field="location",
                )
            )
            return

        has_lat = cleaned.latitude is not None
        has_lon = cleaned.longitude is not None

        if has_lat != has_lon:
            findings.append(
                ValidationFinding(
                    code="INCOMPLETE_COORDINATES",
                    message="only one of latitude/longitude was provided.",
                    field="location",
                )
            )
        elif has_lat and has_lon and not valid_coordinates(cleaned.latitude, cleaned.longitude):
            findings.append(
                ValidationFinding(
                    code="INVALID_COORDINATES",
                    message="latitude/longitude are outside the valid range (-90..90 / -180..180).",
                    field="location",
                )
            )

        if geocode.location_status == "UNRESOLVED":
            findings.append(
                ValidationFinding(
                    code="LOCATION_UNRESOLVED",
                    message="location could not be resolved to an administrative unit; original location preserved.",
                    severity=SeverityFinding.WARNING.value,
                    field="location",
                )
            )
        elif geocode.location_status == "PARTIAL":
            findings.append(
                ValidationFinding(
                    code="LOCATION_PARTIALLY_RESOLVED",
                    message="location was only partially resolved (missing administrative levels).",
                    severity=SeverityFinding.WARNING.value,
                    field="location",
                )
            )

    def _validate_timestamp(self, cleaned: CleanedRecord, findings: list[ValidationFinding]) -> None:
        if cleaned.created_at is None:
            findings.append(
                ValidationFinding(
                    code="MISSING_TIMESTAMP",
                    message="created_at is missing or could not be parsed.",
                    field="created_at",
                )
            )
            return

        now = datetime.now(timezone.utc)
        if cleaned.created_at > now + MAX_FUTURE_SKEW:
            findings.append(
                ValidationFinding(
                    code="FUTURE_TIMESTAMP",
                    message="created_at is in the future.",
                    severity=SeverityFinding.WARNING.value,
                    field="created_at",
                )
            )
        elif cleaned.created_at < MIN_REASONABLE_TIMESTAMP:
            findings.append(
                ValidationFinding(
                    code="IMPLAUSIBLE_TIMESTAMP",
                    message="created_at predates 1970.",
                    field="created_at",
                )
            )

    def _validate_severity(self, cleaned: CleanedRecord, findings: list[ValidationFinding]) -> None:
        if cleaned.severity is None:
            findings.append(
                ValidationFinding(
                    code="MISSING_SEVERITY",
                    message="severity was not provided by Module 2.",
                    severity=SeverityFinding.WARNING.value,
                    field="severity",
                )
            )
            return
        if not cleaned.severity_valid:
            findings.append(
                ValidationFinding(
                    code="INVALID_SEVERITY",
                    message=f"severity {cleaned.severity} is outside the allowed 1-5 range.",
                    field="severity",
                )
            )

    def _validate_description(self, cleaned: CleanedRecord, findings: list[ValidationFinding]) -> None:
        if not cleaned.description:
            findings.append(
                ValidationFinding(
                    code="MISSING_DESCRIPTION",
                    message="description is empty; downstream analytics will have little context.",
                    severity=SeverityFinding.WARNING.value,
                    field="description",
                )
            )

    def _validate_language(self, cleaned: CleanedRecord, findings: list[ValidationFinding]) -> None:
        if not cleaned.language_supported:
            findings.append(
                ValidationFinding(
                    code="UNSUPPORTED_LANGUAGE",
                    message=f"language '{cleaned.language}' is not a recognized language code.",
                    severity=SeverityFinding.WARNING.value,
                    field="language",
                )
            )

    def _validate_normalization(self, normalized: NormalizationResult, findings: list[ValidationFinding]) -> None:
        if normalized.match_source == "fallback":
            findings.append(
                ValidationFinding(
                    code="NORMALIZATION_FAILED",
                    message="no category or sub-category could be normalized from the record.",
                    field="category",
                )
            )
