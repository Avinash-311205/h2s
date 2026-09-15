"""Cleaning service.

Turns a messy Module 2 payload into a normalized in-memory representation. It
never deletes anything: every problem is recorded as a :class:`CleaningIssue`
and the raw payload is carried along so it can be persisted unchanged next to
the processed output.

Conditions are *detected* here, not enforced. Enforcement (and the resulting
``VALID`` / ``NEEDS_REVIEW`` / ``INVALID`` status) belongs to the validation
service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.enums import LANGUAGE_ALIASES, SUPPORTED_LANGUAGES, SeverityFinding
from app.core.geo import valid_coordinates
from app.core.logging import get_logger
from app.core.utils import (
    new_id,
    normalize_text,
    sha256_hex,
    to_utc,
    truncate,
    utcnow,
)

logger = get_logger(__name__)

# Values that carry no information and should be treated as "not provided".
NULL_TOKENS = frozenset(
    {
        "",
        "null",
        "none",
        "nil",
        "n/a",
        "na",
        "na.",
        "nan",
        "undefined",
        "-",
        "--",
        "unknown",
        "not specified",
        "not available",
        "no data",
        "नहीं पता",
        "தெரியவில்லை",
    }
)

MIN_REASONABLE_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_FUTURE_SKEW = timedelta(days=1)

_INT_CONTACT_FIELDS = ("text", "description", "message", "content", "raw_text")
_LOCATION_TEXT_FIELDS = ("location_text", "address", "formatted_address", "place", "text")


@dataclass
class CleaningIssue:
    code: str
    message: str
    severity: str = SeverityFinding.ERROR.value
    field: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": "cleaning",
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "field": self.field,
        }


@dataclass
class CleanedRecord:
    """Normalized, still-unvalidated view of an incoming request."""

    request_id: str
    payload_hash: str
    raw_payload: dict[str, Any] = field(default_factory=dict)

    request_id_valid: bool = True
    language: Optional[str] = None
    language_supported: bool = True

    category_raw: Optional[str] = None
    sub_category_raw: Optional[str] = None
    description: Optional[str] = None
    description_truncated: bool = False

    severity: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_text: Optional[str] = None
    district_hint: Optional[str] = None
    state_hint: Optional[str] = None
    country_hint: Optional[str] = None

    channel: Optional[str] = None
    user_id: Optional[str] = None
    source_module: str = "module-2"

    created_at: Optional[datetime] = None
    created_at_provided: bool = False

    issues: list[CleaningIssue] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    # ---- derived helpers -------------------------------------------------
    @property
    def coordinates_present(self) -> bool:
        return self.latitude is not None or self.longitude is not None

    @property
    def coordinates_valid(self) -> bool:
        return valid_coordinates(self.latitude, self.longitude)

    @property
    def location_present(self) -> bool:
        return self.coordinates_present or bool(self.location_text)

    @property
    def severity_valid(self) -> bool:
        if self.severity is None:
            return False
        return 1 <= self.severity <= 5

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == SeverityFinding.ERROR.value for issue in self.issues)

    def issue_dicts(self) -> list[dict[str, Any]]:
        return [issue.to_dict() for issue in self.issues]

    def errors(self) -> list[CleaningIssue]:
        return [issue for issue in self.issues if issue.severity == SeverityFinding.ERROR.value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "language": self.language,
            "category_raw": self.category_raw,
            "sub_category_raw": self.sub_category_raw,
            "description": self.description,
            "severity": self.severity,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "location_text": self.location_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "issues": self.issue_dicts(),
            "missing_fields": list(self.missing_fields),
        }


class CleaningService:
    """Pure, side-effect free cleaning of an incoming record."""

    def clean(self, payload: dict[str, Any]) -> CleanedRecord:
        raw: dict[str, Any] = dict(payload or {})
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}

        record = CleanedRecord(
            request_id="",
            payload_hash=sha256_hex(raw),
            raw_payload=raw,
            source_module=self._clean_optional_text(raw.get("source_module"), "source_module") or "module-2",
            channel=self._clean_optional_text(raw.get("channel") or raw.get("source_channel"), "channel"),
            user_id=self._clean_optional_text(raw.get("user_id") or raw.get("citizen_id"), "user_id"),
        )

        record.request_id, record.request_id_valid = self._clean_request_id(raw.get("request_id"), record)

        record.language, record.language_supported = self._clean_language(raw.get("language"), record)

        record.description, record.description_truncated = self._clean_description(raw, record)

        record.category_raw = self._clean_optional_text(raw.get("category") or raw.get("category_raw"), "category")
        record.sub_category_raw = self._clean_optional_text(
            raw.get("sub_category") or raw.get("subcategory") or raw.get("sub_category_raw"), "sub_category"
        )

        record.severity = self._clean_severity(raw.get("severity"), record)

        record.latitude, record.longitude = self._clean_coordinates(raw, location, record)

        record.location_text = self._clean_location_text(raw, location, record)
        record.district_hint = self._clean_optional_text(
            location.get("district") or raw.get("district"), "district"
        )
        record.state_hint = self._clean_optional_text(location.get("state") or raw.get("state"), "state")
        record.country_hint = self._clean_optional_text(location.get("country") or raw.get("country"), "country")

        record.created_at, record.created_at_provided = self._clean_timestamp(
            raw.get("created_at") or raw.get("timestamp") or raw.get("reported_at"), record
        )

        self._detect_missing_mandatory_fields(record)
        return record

    # ------------------------------------------------------------------
    # field cleaners
    # ------------------------------------------------------------------
    def _clean_request_id(self, value: Any, record: CleanedRecord) -> tuple[str, bool]:
        text = self._as_text(value)
        if text is None:
            generated = new_id("REQ-UNASSIGNED")
            record.issues.append(
                CleaningIssue(
                    code="MISSING_REQUEST_ID",
                    message="request_id is missing; a placeholder identifier was generated so no data is lost.",
                    field="request_id",
                )
            )
            return generated, False

        cleaned = text.strip()
        if not cleaned.replace("-", "").replace("_", "").isalnum() or len(cleaned) > 64:
            record.issues.append(
                CleaningIssue(
                    code="MALFORMED_REQUEST_ID",
                    message="request_id contains unsupported characters or is too long; a placeholder was generated.",
                    field="request_id",
                )
            )
            return new_id("REQ-UNASSIGNED"), False
        return cleaned, True

    def _clean_language(self, value: Any, record: CleanedRecord) -> tuple[Optional[str], bool]:
        text = self._as_text(value)
        if text is None:
            record.issues.append(
                CleaningIssue(
                    code="MISSING_LANGUAGE",
                    message="language was not provided; assuming the record is language-agnostic.",
                    severity=SeverityFinding.WARNING.value,
                    field="language",
                )
            )
            return None, False

        key = text.strip().casefold()
        code = LANGUAGE_ALIASES.get(key) or LANGUAGE_ALIASES.get(key.split("-")[0])
        if code is None:
            record.issues.append(
                CleaningIssue(
                    code="UNKNOWN_LANGUAGE",
                    message=f"language '{text}' is not a recognized ISO 639-1 code or supported name.",
                    severity=SeverityFinding.WARNING.value,
                    field="language",
                )
            )
            truncated, _ = truncate(key, 10)
            return truncated, False

        if code not in SUPPORTED_LANGUAGES:
            # Recognized language, but the normalization dictionary has no
            # vocabulary for it yet (extend the JSON to add support).
            record.issues.append(
                CleaningIssue(
                    code="LANGUAGE_NOT_SUPPORTED",
                    message=(
                        f"language '{code}' is recognized but has no normalization vocabulary yet; "
                        "keywords will fall back to the default languages."
                    ),
                    severity=SeverityFinding.WARNING.value,
                    field="language",
                )
            )
            return code, False

        return code, True

    def _clean_description(self, raw: dict[str, Any], record: CleanedRecord) -> tuple[Optional[str], bool]:
        value: Any = None
        for key in _INT_CONTACT_FIELDS:
            candidate = raw.get(key)
            if isinstance(candidate, str) and candidate.strip():
                value = candidate
                break

        if value is None:
            return None, False

        text = normalize_text(value)
        if not text or text.casefold() in NULL_TOKENS:
            return None, False

        truncated, was_truncated = truncate(text, settings.max_description_length)
        if was_truncated:
            record.issues.append(
                CleaningIssue(
                    code="DESCRIPTION_TRUNCATED",
                    message=f"description exceeded {settings.max_description_length} characters and was truncated.",
                    severity=SeverityFinding.WARNING.value,
                    field="description",
                )
            )
        return truncated, was_truncated

    def _clean_optional_text(self, value: Any, field_name: str) -> Optional[str]:
        text = self._as_text(value)
        if text is None:
            return None
        text = normalize_text(text)
        if not text or text.casefold() in NULL_TOKENS:
            return None
        truncated, _ = truncate(text, settings.max_text_field_length)
        return truncated

    def _clean_severity(self, value: Any, record: CleanedRecord) -> Optional[float]:
        number = self._coerce_float(value)
        if number is None:
            if value not in (None, ""):
                record.issues.append(
                    CleaningIssue(
                        code="MALFORMED_SEVERITY",
                        message=f"severity '{value}' is not numeric and was ignored.",
                        severity=SeverityFinding.WARNING.value,
                        field="severity",
                    )
                )
            return None
        if not 1 <= number <= 5:
            record.issues.append(
                CleaningIssue(
                    code="SEVERITY_OUT_OF_RANGE",
                    message=f"severity {number} is outside the allowed 1-5 range.",
                    field="severity",
                )
            )
        return number

    def _clean_coordinates(
        self,
        raw: dict[str, Any],
        location: dict[str, Any],
        record: CleanedRecord,
    ) -> tuple[Optional[float], Optional[float]]:
        lat_value = location.get("latitude", raw.get("latitude"))
        lon_value = location.get("longitude", raw.get("longitude"))
        lat = lon = None

        if lat_value not in (None, ""):
            lat = self._coerce_float(lat_value)
            if lat is None:
                record.issues.append(
                    CleaningIssue(
                        code="MALFORMED_COORDINATE",
                        message=f"latitude '{lat_value}' is not numeric.",
                        field="latitude",
                    )
                )
            elif not -90 <= lat <= 90:
                record.issues.append(
                    CleaningIssue(
                        code="INVALID_LATITUDE",
                        message=f"latitude {lat} is outside the valid -90..90 range.",
                        field="latitude",
                    )
                )

        if lon_value not in (None, ""):
            lon = self._coerce_float(lon_value)
            if lon is None:
                record.issues.append(
                    CleaningIssue(
                        code="MALFORMED_COORDINATE",
                        message=f"longitude '{lon_value}' is not numeric.",
                        field="longitude",
                    )
                )
            elif not -180 <= lon <= 180:
                record.issues.append(
                    CleaningIssue(
                        code="INVALID_LONGITUDE",
                        message=f"longitude {lon} is outside the valid -180..180 range.",
                        field="longitude",
                    )
                )

        # A single coordinate is meaningless for geospatial work; flag it but
        # keep the value available for auditing.
        if (lat is None) != (lon is None):
            record.issues.append(
                CleaningIssue(
                    code="INCOMPLETE_COORDINATES",
                    message="only one of latitude/longitude was provided.",
                    severity=SeverityFinding.WARNING.value,
                    field="location",
                )
            )

        return lat, lon

    def _clean_location_text(
        self,
        raw: dict[str, Any],
        location: dict[str, Any],
        record: CleanedRecord,
    ) -> Optional[str]:
        candidates: list[Any] = []
        for key in _LOCATION_TEXT_FIELDS:
            if key in location:
                candidates.append(location.get(key))
        for key in ("location_text", "location", "address", "place"):
            if key in raw:
                candidates.append(raw.get(key))

        for candidate in candidates:
            if isinstance(candidate, str):
                text = normalize_text(candidate)
                if text and text.casefold() not in NULL_TOKENS:
                    truncated, _ = truncate(text, settings.max_text_field_length)
                    return truncated
        return None

    def _clean_timestamp(self, value: Any, record: CleanedRecord) -> tuple[Optional[datetime], bool]:
        if value in (None, ""):
            return None, False

        parsed: Optional[datetime] = None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = self._from_epoch(float(value))
        elif isinstance(value, str):
            parsed = self._parse_datetime_string(value)

        if parsed is None:
            record.issues.append(
                CleaningIssue(
                    code="INVALID_TIMESTAMP",
                    message=f"timestamp '{value}' could not be parsed as ISO 8601 or epoch seconds.",
                    field="created_at",
                )
            )
            return None, True

        parsed = to_utc(parsed)
        if parsed > utcnow() + MAX_FUTURE_SKEW:
            record.issues.append(
                CleaningIssue(
                    code="FUTURE_TIMESTAMP",
                    message="timestamp is in the future; treated as suspicious but retained.",
                    severity=SeverityFinding.WARNING.value,
                    field="created_at",
                )
            )
        elif parsed < MIN_REASONABLE_TIMESTAMP:
            record.issues.append(
                CleaningIssue(
                    code="IMPLAUSIBLE_TIMESTAMP",
                    message="timestamp predates 1970 and is likely wrong.",
                    field="created_at",
                )
            )
        return parsed, True

    def _detect_missing_mandatory_fields(self, record: CleanedRecord) -> None:
        missing: list[str] = []
        if not record.request_id_valid:
            missing.append("request_id")
        if not record.category_raw:
            missing.append("category")
        if not record.location_present:
            missing.append("location")
        if record.created_at is None:
            missing.append("created_at")

        record.missing_fields = missing
        for field_name in missing:
            # request_id already produced a dedicated issue.
            if field_name == "request_id":
                continue
            record.issues.append(
                CleaningIssue(
                    code="MISSING_MANDATORY_FIELD",
                    message=f"mandatory field '{field_name}' is missing or unusable.",
                    field=field_name,
                )
            )

    # ------------------------------------------------------------------
    # coercion helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _as_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return str(value)
        return None

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip().replace(",", ".")
            if not text or text.casefold() in NULL_TOKENS:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    @staticmethod
    def _from_epoch(value: float) -> Optional[datetime]:
        # Heuristic: values above 1e11 are milliseconds.
        seconds = value / 1000.0 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _parse_datetime_string(value: str) -> Optional[datetime]:
        text = value.strip()
        if not text:
            return None
        candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
        return None
