"""Citizen request / civic record / location / processing error repository."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.enums import ProcessingStatus
from app.core.geo import build_point_wkt, valid_coordinates
from app.core.logging import get_logger
from app.core.utils import sha256_hex, utcnow
from app.models.citizen_request import CitizenRequest
from app.models.civic_record import ProcessedCivicRecord
from app.models.location import Location
from app.models.processing_error import ProcessingError
from app.services.cleaning_service import CleanedRecord
from app.services.geocoding_service import GeocodeResult

logger = get_logger(__name__)


def location_key(latitude: Optional[float], longitude: Optional[float], district: Optional[str] = None) -> str:
    """Stable de-duplication key for the ``locations`` table."""
    if valid_coordinates(latitude, longitude):
        return sha256_hex(f"{latitude:.5f}:{longitude:.5f}")
    return sha256_hex(f"{district or ''}|{''}")


class CivicRepository:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # citizen_requests
    # ------------------------------------------------------------------
    def upsert_citizen_request(self, cleaned: CleanedRecord) -> CitizenRequest:
        existing = self.get_citizen_request(cleaned.request_id)
        if existing is not None:
            # Same request arriving again: refresh the snapshot but keep the id.
            existing.raw_payload = cleaned.raw_payload
            existing.payload_hash = cleaned.payload_hash
            existing.updated_at = utcnow()
            self.db.flush()
            return existing

        record = CitizenRequest(
            request_id=cleaned.request_id,
            source_module=cleaned.source_module,
            channel=cleaned.channel,
            user_id=cleaned.user_id,
            language=cleaned.language,
            raw_text=cleaned.description,
            raw_category=cleaned.category_raw,
            raw_sub_category=cleaned.sub_category_raw,
            raw_severity=cleaned.severity,
            raw_location_text=cleaned.location_text,
            raw_latitude=cleaned.latitude,
            raw_longitude=cleaned.longitude,
            raw_payload=cleaned.raw_payload,
            payload_hash=cleaned.payload_hash,
            source_created_at=cleaned.created_at,
            received_at=utcnow(),
            status=ProcessingStatus.NEEDS_REVIEW.value,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_citizen_request(self, request_id: str) -> Optional[CitizenRequest]:
        return self.db.query(CitizenRequest).filter(CitizenRequest.request_id == request_id).first()

    def set_citizen_request_status(self, request_id: str, status: str) -> None:
        record = self.get_citizen_request(request_id)
        if record is not None:
            record.status = status
            record.updated_at = utcnow()
            self.db.flush()

    # ------------------------------------------------------------------
    # processed_civic_records
    # ------------------------------------------------------------------
    def get_processed(self, request_id: str) -> Optional[ProcessedCivicRecord]:
        return (
            self.db.query(ProcessedCivicRecord)
            .filter(ProcessedCivicRecord.request_id == request_id)
            .first()
        )

    def create_processed(self, **fields: Any) -> ProcessedCivicRecord:
        record = ProcessedCivicRecord(**fields)
        self.db.add(record)
        self.db.flush()
        return record

    def update_processed(self, record: ProcessedCivicRecord, **fields: Any) -> ProcessedCivicRecord:
        for key, value in fields.items():
            setattr(record, key, value)
        record.updated_at = utcnow()
        self.db.flush()
        return record

    def list_processed_by_issue_group(self, issue_group_id: str) -> Sequence[ProcessedCivicRecord]:
        return (
            self.db.query(ProcessedCivicRecord)
            .filter(ProcessedCivicRecord.issue_group_id == issue_group_id)
            .order_by(ProcessedCivicRecord.processed_at.asc())
            .all()
        )

    # ------------------------------------------------------------------
    # locations
    # ------------------------------------------------------------------
    def upsert_location(
        self,
        geocode: GeocodeResult,
        location_text: Optional[str] = None,
        increment: bool = True,
    ) -> Optional[Location]:
        if not geocode.has_coordinates and not geocode.has_admin:
            return None

        key = location_key(geocode.latitude, geocode.longitude, geocode.district)
        existing = self.db.query(Location).filter(Location.location_key == key).first()
        if existing is not None:
            if increment:
                existing.report_count = (existing.report_count or 0) + 1
            existing.location_status = geocode.location_status or existing.location_status
            existing.provider = geocode.provider or existing.provider
            existing.confidence = geocode.confidence if geocode.confidence is not None else existing.confidence
            existing.village = existing.village or geocode.village
            existing.ward = existing.ward or geocode.ward
            existing.taluk = existing.taluk or geocode.taluk
            existing.district = existing.district or geocode.district
            existing.state = existing.state or geocode.state
            existing.country = existing.country or geocode.country
            existing.pincode = existing.pincode or geocode.pincode
            existing.updated_at = utcnow()
            self.db.flush()
            return existing

        location = Location(
            location_key=key,
            latitude=geocode.latitude,
            longitude=geocode.longitude,
            geom_wkt=build_point_wkt(geocode.longitude, geocode.latitude),
            village=geocode.village,
            ward=geocode.ward,
            taluk=geocode.taluk,
            district=geocode.district,
            state=geocode.state,
            country=geocode.country,
            pincode=geocode.pincode,
            location_text=location_text,
            location_status=geocode.location_status,
            provider=geocode.provider,
            confidence=geocode.confidence,
            report_count=1,
        )
        self.db.add(location)
        self.db.flush()
        return location

    # ------------------------------------------------------------------
    # processing_errors
    # ------------------------------------------------------------------
    def add_processing_error(
        self,
        stage: str,
        error_code: str,
        message: str,
        request_id: Optional[str] = None,
        severity: str = "error",
        is_fatal: bool = False,
        details: Optional[dict[str, Any]] = None,
    ) -> ProcessingError:
        error = ProcessingError(
            request_id=request_id,
            stage=stage,
            error_code=error_code,
            message=message,
            severity=severity,
            is_fatal=is_fatal,
            details=details,
        )
        self.db.add(error)
        self.db.flush()
        return error

    def delete_processing_errors(self, request_id: str) -> None:
        self.db.query(ProcessingError).filter(ProcessingError.request_id == request_id).delete()
        self.db.flush()

    def list_processing_errors(self, request_id: str) -> Sequence[ProcessingError]:
        return (
            self.db.query(ProcessingError)
            .filter(ProcessingError.request_id == request_id)
            .order_by(ProcessingError.created_at.asc())
            .all()
        )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
