"""Pydantic v2 schemas for the Module 3 API.

Input schemas are deliberately *lenient*: Module 3's job is to detect and flag
bad data, not to reject a citizen's report at the HTTP boundary. Hard limits
(``max_length``, absurd coordinate magnitudes) protect the service, while
range/semantic problems are reported through ``data_quality_status``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT = 20_000
COORDINATE_SANITY_LIMIT = 1_000_000.0


class LocationInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    latitude: Optional[float] = Field(None, ge=-COORDINATE_SANITY_LIMIT, le=COORDINATE_SANITY_LIMIT)
    longitude: Optional[float] = Field(None, ge=-COORDINATE_SANITY_LIMIT, le=COORDINATE_SANITY_LIMIT)
    village: Optional[str] = Field(None, max_length=160)
    ward: Optional[str] = Field(None, max_length=160)
    taluk: Optional[str] = Field(None, max_length=160)
    district: Optional[str] = Field(None, max_length=160)
    state: Optional[str] = Field(None, max_length=160)
    country: Optional[str] = Field(None, max_length=160)
    pincode: Optional[str] = Field(None, max_length=20)
    location_text: Optional[str] = Field(None, max_length=2_000)
    address: Optional[str] = Field(None, max_length=2_000)
    text: Optional[str] = Field(None, max_length=2_000)


class Module2RecordIn(BaseModel):
    """Output of Module 2 (AI Understanding) as accepted by Module 3."""

    model_config = ConfigDict(extra="allow")

    request_id: Optional[str] = Field(None, max_length=64)
    language: Optional[str] = Field(None, max_length=20)
    category: Optional[str] = Field(None, max_length=80)
    sub_category: Optional[str] = Field(None, max_length=80)
    subcategory: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=MAX_TEXT)
    text: Optional[str] = Field(None, max_length=MAX_TEXT)
    severity: Optional[float] = Field(None, ge=-COORDINATE_SANITY_LIMIT, le=COORDINATE_SANITY_LIMIT)

    location: Optional[LocationInput] = None
    latitude: Optional[float] = Field(None, ge=-COORDINATE_SANITY_LIMIT, le=COORDINATE_SANITY_LIMIT)
    longitude: Optional[float] = Field(None, ge=-COORDINATE_SANITY_LIMIT, le=COORDINATE_SANITY_LIMIT)
    location_text: Optional[str] = Field(None, max_length=2_000)

    channel: Optional[str] = Field(None, max_length=50)
    user_id: Optional[str] = Field(None, max_length=255)
    source_module: Optional[str] = Field(None, max_length=32)

    created_at: Optional[Union[datetime, str, int, float]] = None
    timestamp: Optional[Union[datetime, str, int, float]] = None
    reported_at: Optional[Union[datetime, str, int, float]] = None


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------
class LocationOut(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    village: Optional[str] = None
    ward: Optional[str] = None
    taluk: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    location_status: Optional[str] = None
    provider: Optional[str] = None


class ProcessingIssueOut(BaseModel):
    stage: Optional[str] = None
    code: str
    message: str
    severity: Optional[str] = None
    field: Optional[str] = None


class EventOut(BaseModel):
    event_id: str
    event_type: str
    channel: str
    status: str
    attempts: int = 0
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    last_error: Optional[str] = None


class CivicRecordOut(BaseModel):
    request_id: str
    category: str
    sub_category: str
    description: Optional[str] = None
    language: Optional[str] = None
    severity: Optional[int] = None

    location: LocationOut
    location_status: str

    issue_group_id: Optional[str] = None
    is_duplicate: bool = False
    report_count: int = 1

    data_quality_score: float
    data_quality_status: str
    processing_status: str
    normalization_confidence: float = 0.0
    normalization_source: Optional[str] = None
    matched_keywords: list[str] = Field(default_factory=list)

    processing_version: str
    reprocess_count: int = 0

    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None

    processing_issues: list[ProcessingIssueOut] = Field(default_factory=list)
    validation_findings: list[ProcessingIssueOut] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class ProcessResponse(BaseModel):
    idempotent: bool = False
    payload_changed: bool = False
    event_published: bool = False
    event_type: Optional[str] = None
    errors: list[ProcessingIssueOut] = Field(default_factory=list)
    record: CivicRecordOut


class ProcessingStatusOut(BaseModel):
    request_id: str
    processing_status: str
    data_quality_status: str
    data_quality_score: float
    issue_group_id: Optional[str] = None
    is_duplicate: bool = False
    processed_at: Optional[datetime] = None
    reprocess_count: int = 0
    errors: list[ProcessingIssueOut] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)


class IssueGroupOut(BaseModel):
    issue_group_id: str
    category: str
    sub_category: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    status: str
    report_count: int
    severity_avg: Optional[float] = None
    severity_max: Optional[float] = None
    first_reported_at: Optional[datetime] = None
    last_reported_at: Optional[datetime] = None
    representative_request_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    timestamp: datetime
    checks: dict[str, Any]


class EventRetryOut(BaseModel):
    attempted: int
    published: int
    failed: int
    pending_before: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ORM -> schema mappers
# ---------------------------------------------------------------------------
def _findings(values: Optional[list[dict[str, Any]]]) -> list[ProcessingIssueOut]:
    return [ProcessingIssueOut(**item) for item in (values or [])]


def location_out(record: Any) -> LocationOut:
    return LocationOut(
        latitude=record.latitude,
        longitude=record.longitude,
        village=record.village,
        ward=record.ward,
        taluk=record.taluk,
        district=record.district,
        state=record.state,
        country=record.country,
        pincode=record.pincode,
        location_status=record.location_status,
        provider=record.geocoding_provider,
    )


def civic_record_out(record: Any) -> CivicRecordOut:
    report_count = 1
    group = getattr(record, "issue_group", None)
    if group is not None:
        report_count = group.report_count or 1

    return CivicRecordOut(
        request_id=record.request_id,
        category=record.category,
        sub_category=record.sub_category,
        description=record.description,
        language=record.language,
        severity=record.severity,
        location=location_out(record),
        location_status=record.location_status,
        issue_group_id=record.issue_group_id,
        is_duplicate=bool(record.is_duplicate),
        report_count=report_count,
        data_quality_score=record.data_quality_score,
        data_quality_status=record.data_quality_status,
        processing_status=record.processing_status,
        normalization_confidence=record.normalization_confidence,
        normalization_source=record.normalization_source,
        matched_keywords=list(record.matched_keywords or []),
        processing_version=record.processing_version,
        reprocess_count=record.reprocess_count or 0,
        created_at=record.created_at,
        processed_at=record.processed_at,
        processing_issues=_findings(record.processing_issues),
        validation_findings=_findings(record.validation_findings),
        raw_data=record.raw_data or {},
    )


def issue_group_out(group: Any) -> IssueGroupOut:
    return IssueGroupOut(
        issue_group_id=group.issue_group_id,
        category=group.category,
        sub_category=group.sub_category,
        latitude=group.latitude,
        longitude=group.longitude,
        district=group.district,
        state=group.state,
        country=group.country,
        status=group.status,
        report_count=group.report_count,
        severity_avg=group.severity_avg,
        severity_max=group.severity_max,
        first_reported_at=group.first_reported_at,
        last_reported_at=group.last_reported_at,
        representative_request_id=group.representative_request_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def event_out(event: Any) -> EventOut:
    return EventOut(
        event_id=event.event_id,
        event_type=event.event_type,
        channel=event.channel,
        status=event.status,
        attempts=event.attempts or 0,
        created_at=event.created_at,
        published_at=event.published_at,
        last_error=event.last_error,
    )
