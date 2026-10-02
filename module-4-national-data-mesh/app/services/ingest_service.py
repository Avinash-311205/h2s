"""Ingest Module 3 civic records into the mesh (Module 3 -> Module 4).

Two writes happen per accepted record, and the second is what makes the mesh
auditable:

1. **Aggregate** - the record is folded into ``citizen_demand`` at
   ``(ward_code, sector, category, window_days)``. This is what the gap score is
   computed from, exactly as before.
2. **Lineage** - the individual request is recorded in ``civic_record_lineage``
   with its ``request_id`` and the upstream ``event_id``. Aggregating without
   this step would make every gap score unauditable, because the aggregate
   discards the submissions that produced it.

Idempotency is keyed on ``request_id``: a redelivered event rewrites the lineage
row and does **not** add a second complaint to the aggregate.

Sector mapping: Module 2's ``Category`` taxonomy is deliberately identical to
this module's ``Sector`` taxonomy (same values: ROAD, WATER, ...), so a
recognised category maps straight across. Anything unrecognised becomes
``UNKNOWN`` rather than being forced into a sector it does not belong to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.enums import Sector
from app.core.logging import get_logger
from app.core.utils import utcnow
from app.models.mesh_tables import CitizenDemand, CivicRecordLineage
from app.repositories.mesh_repository import MeshRepository
from app.services import geo_service

logger = get_logger(__name__)

SOURCE_MODULE = "module-3-civic-data-processing"
DEFAULT_WINDOW_DAYS = 30


@dataclass
class IngestResult:
    """Outcome of one ingest attempt, returned to the caller and the event log."""

    request_id: str
    accepted: bool
    reason: str
    duplicate: bool = False
    ward_code: Optional[str] = None
    sector: str = Sector.UNKNOWN.value
    category: str = "UNKNOWN"
    severity: int = 1
    source_event_id: Optional[str] = None
    lineage_id: Optional[int] = None
    counted_in_demand: bool = False
    complaint_count: Optional[int] = None
    event_type: Optional[str] = None


def sector_for_category(category: Optional[str]) -> str:
    """Map a Module 2/3 category onto a mesh sector.

    The two taxonomies share their values by design, so this is an identity map
    for known categories. Unknown categories are recorded as ``UNKNOWN`` rather
    than being guessed into a sector.
    """
    if not category:
        return Sector.UNKNOWN.value
    candidate = str(category).strip().upper()
    try:
        return Sector(candidate).value
    except ValueError:
        return Sector.UNKNOWN.value


def _as_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _clamp_severity(value: Any) -> int:
    try:
        severity = int(round(float(value)))
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, severity))


class CivicRecordIngestService:
    """Applies one Module 3 event to the mesh."""

    def __init__(self, session: Session):
        self.session = session
        self.repo = MeshRepository(session)

    # ------------------------------------------------------------------
    def ingest(self, envelope: dict[str, Any]) -> IngestResult:
        """Ingest one published Module 3 event envelope."""
        if not isinstance(envelope, dict):
            return IngestResult(
                request_id="", accepted=False, reason="envelope is not an object"
            )

        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            return IngestResult(
                request_id="", accepted=False, reason="envelope payload is not an object"
            )

        request_id = (
            envelope.get("correlation_id")
            or envelope.get("request_id")
            or payload.get("request_id")
        )
        if not request_id:
            return IngestResult(
                request_id="", accepted=False, reason="event carries no correlation_id"
            )

        event_type = (
            envelope.get("event")
            or envelope.get("event_type")
            or payload.get("event")
            or "CIVIC_RECORD_PROCESSED"
        )
        source_event_id = envelope.get("event_id") or payload.get("event_id")
        schema_version = envelope.get("schema_version") or payload.get("schema_version")

        quality_status = payload.get("data_quality_status")
        # A rejected record is still worth keeping for provenance and review,
        # but it must not inflate demand or the gap score.
        counted = quality_status != "REJECTED"

        category = (payload.get("category") or "UNKNOWN").strip().upper()[:40]
        sector = sector_for_category(category)
        severity = _clamp_severity(payload.get("severity"))

        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
        ward_code, district, location_status = self._resolve_location(payload, latitude, longitude)

        duplicate = self.repo.get_lineage(request_id) is not None
        observed_at = _as_datetime(payload.get("processed_at")) or utcnow()

        # --- lineage first: it is the record of what we accepted ----------
        lineage = self.repo.upsert_lineage(
            request_id=request_id,
            source_event_id=source_event_id,
            issue_group_id=envelope.get("issue_group_id") or payload.get("issue_group_id"),
            ward_code=ward_code,
            district=district,
            location_status=location_status,
            sector=sector,
            category=category,
            sub_category=(payload.get("sub_category") or "UNCLASSIFIED")[:60],
            severity=severity,
            language=(payload.get("language") or None),
            data_quality_score=float(payload.get("data_quality_score") or 0.0),
            data_quality_status=quality_status,
            source_module=SOURCE_MODULE,
            event_type=event_type,
            schema_version=schema_version,
            payload=payload,
            event_timestamp=_as_datetime(envelope.get("timestamp")),
            processed_at=observed_at,
        )

        # --- aggregate, only for new, ward-assignable, non-rejected records --
        # A replay must not add a second complaint: citizen_demand is a running
        # total, so it cannot absorb a duplicate the way the lineage upsert can.
        demand_count = None
        if duplicate:
            counted_in_demand = bool(lineage.counted_in_demand)
        elif counted and ward_code:
            demand = self._apply_demand(
                ward_code=ward_code,
                sector=sector,
                category=category,
                sub_category=(payload.get("sub_category") or "UNCLASSIFIED")[:60],
                severity=severity,
                language=(payload.get("language") or None),
                observed_at=observed_at,
            )
            demand_count = demand.complaint_count if demand else None
            counted_in_demand = True
        else:
            counted_in_demand = False
            if not counted:
                logger.info(
                    "ingest_not_counted",
                    extra={"request_id": request_id, "quality_status": quality_status},
                )

        lineage.counted_in_demand = counted_in_demand
        self.session.commit()

        logger.info(
            "civic_record_ingested",
            extra={
                "request_id": request_id,
                "source_event_id": source_event_id,
                "ward_code": ward_code,
                "sector": sector,
                "category": category,
                "severity": severity,
                "duplicate": duplicate,
                "counted_in_demand": counted_in_demand,
            },
        )

        return IngestResult(
            request_id=request_id,
            accepted=True,
            reason="duplicate_refreshed" if duplicate else "ingested",
            duplicate=duplicate,
            ward_code=ward_code,
            sector=sector,
            category=category,
            severity=severity,
            source_event_id=source_event_id,
            lineage_id=lineage.id,
            counted_in_demand=counted_in_demand,
            complaint_count=demand_count,
            event_type=event_type,
        )

    # ------------------------------------------------------------------
    def _resolve_location(
        self, payload: dict[str, Any], latitude: Any, longitude: Any
    ) -> tuple[Optional[str], Optional[str], str]:
        """Prefer Module 3's resolved admin units; fall back to a point-in-ward."""
        district = payload.get("district") or None
        ward = (payload.get("ward") or "").strip() if isinstance(payload.get("ward"), str) else None

        if ward:
            resolved = self.repo.get_ward_by_name(ward, district=district)
            if resolved is not None:
                return resolved.ward_code, resolved.district, payload.get("location_status") or "MODULE3_WARD"

        try:
            lat = float(latitude) if latitude is not None else None
            lon = float(longitude) if longitude is not None else None
        except (TypeError, ValueError):
            lat = lon = None

        if lat is None or lon is None:
            return None, district, payload.get("location_status") or "NO_COORDINATES"

        wards = self.repo.list_wards()
        assignment = geo_service.assign_to_ward(lat, lon, wards)
        if assignment.ward_code:
            resolved = self.repo.get_ward(assignment.ward_code)
            return assignment.ward_code, (resolved.district if resolved else district), assignment.method
        return None, district, assignment.method

    def _apply_demand(
        self,
        *,
        ward_code: str,
        sector: str,
        category: str,
        sub_category: str,
        severity: int,
        language: Optional[str],
        observed_at: datetime,
    ) -> Optional[CitizenDemand]:
        """Increment the aggregate demand row for this ward/sector/category."""
        return self.repo.apply_demand_increment(
            ward_code=ward_code,
            sector=sector,
            category=category,
            sub_category=sub_category,
            severity=severity,
            language=language,
            observed_at=observed_at,
            window_days=DEFAULT_WINDOW_DAYS,
        )
