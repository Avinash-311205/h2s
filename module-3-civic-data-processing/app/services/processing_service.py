"""Processing orchestrator.

The single entry point that turns a Module 2 payload into a stored, standardized
civic record:

    cleaning -> normalization -> geocoding -> deduplication -> validation
    -> quality scoring -> persistence -> event publication

Design rules enforced here:

* **Nothing is lost.** The raw request is committed before any processing stage
  runs, and every stage is isolated so a downstream outage cannot destroy data.
* **Idempotent.** Processing the same ``request_id`` twice returns the original
  record instead of creating a second one.
* **Fault tolerant.** Geocoding failures, Redis outages and duplicate-detection
  errors degrade the record's quality status rather than failing the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.enums import DataQualityStatus, ProcessingStatus
from app.core.geo import valid_coordinates
from app.core.logging import bind_request_context, get_logger, set_stage
from app.core.utils import utcnow
from app.events.publisher import CivicEvent, OutboxEventPublisher, build_event_for_record, publisher_for_session
from app.models.civic_record import ProcessedCivicRecord
from app.models.issue_group import IssueGroup
from app.repositories.civic_repository import CivicRepository
from app.repositories.issue_repository import IssueRepository
from app.services.cleaning_service import CleanedRecord, CleaningService
from app.services.deduplication_service import (
    DeduplicationCandidate,
    DeduplicationService,
    IssueAssignment,
)
from app.services.geocoding_service import GeocodeResult, GeocodingService
from app.services.normalization_service import NormalizationResult, NormalizationService
from app.services.quality_service import QualityService
from app.services.validation_service import ValidationResult, ValidationService

logger = get_logger(__name__)

PROCESSING_VERSION = "1.0.0"


class ProcessingFailure(Exception):
    """Raised when a record cannot be processed at all (still never silently dropped)."""

    def __init__(
        self,
        message: str,
        request_id: Optional[str] = None,
        stage: Optional[str] = None,
        fatal_errors: Optional[list[dict[str, Any]]] = None,
    ):
        super().__init__(message)
        self.request_id = request_id
        self.stage = stage
        self.fatal_errors = fatal_errors or []


@dataclass
class ProcessingOutcome:
    record: ProcessedCivicRecord
    idempotent: bool = False
    payload_changed: bool = False
    event: Optional[CivicEvent] = None
    event_published: bool = False
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def request_id(self) -> str:
        return self.record.request_id


@dataclass
class _PipelineResult:
    cleaned: CleanedRecord
    normalized: NormalizationResult
    geocode: GeocodeResult
    validation: ValidationResult
    quality_score: float
    quality_status: str
    quality_factors: dict[str, float]
    assignment: IssueAssignment
    location_id: Optional[int]
    issues: list[dict[str, Any]] = field(default_factory=list)


class CivicProcessingService:
    """Orchestrates the full Module 3 pipeline for a single record."""

    def __init__(
        self,
        db: Session,
        cleaning: Optional[CleaningService] = None,
        normalization: Optional[NormalizationService] = None,
        geocoding: Optional[GeocodingService] = None,
        validation: Optional[ValidationService] = None,
        quality: Optional[QualityService] = None,
        deduplication: Optional[DeduplicationService] = None,
        publisher: Optional[OutboxEventPublisher] = None,
    ):
        self.db = db
        self.civic = CivicRepository(db)
        self.issues = IssueRepository(db)

        self.cleaning = cleaning or CleaningService()
        self.normalization = normalization or NormalizationService()
        self.geocoding = geocoding or GeocodingService()
        self.validation = validation or ValidationService()
        self.quality = quality or QualityService()
        self.deduplication = deduplication or DeduplicationService(self.issues)
        self.publisher = publisher or publisher_for_session(db)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def process(self, payload: dict[str, Any]) -> ProcessingOutcome:
        cleaned = self.cleaning.clean(payload)
        bind_request_context(cleaned.request_id, "process")

        existing = self.civic.get_processed(cleaned.request_id)
        if existing is not None:
            payload_changed = existing.payload_hash != cleaned.payload_hash
            if payload_changed:
                logger.warning(
                    "idempotent_processing_payload_changed",
                    extra={"request_id": cleaned.request_id, "stored_hash": existing.payload_hash},
                )
            logger.info("idempotent_processing_hit", extra={"request_id": cleaned.request_id})
            return self._outcome(existing, idempotent=True, payload_changed=payload_changed)

        # Persist the raw citizen request first: from here on, data is durable
        # even if every downstream stage fails.
        set_stage("persist_raw")
        try:
            self.civic.upsert_citizen_request(cleaned)
            self.civic.commit()
        except Exception as exc:
            self.civic.rollback()
            logger.error("raw_persist_failed", extra={"request_id": cleaned.request_id, "error": str(exc)})
            raise ProcessingFailure(
                "Failed to persist the incoming citizen request.",
                request_id=cleaned.request_id,
                stage="persist_raw",
            ) from exc

        set_stage("pipeline")
        result = self._run_pipeline(cleaned, persist_errors=True)

        record = self._persist_record(cleaned, result, reprocessed=False)
        self.civic.set_citizen_request_status(cleaned.request_id, record.processing_status)

        event = self._enqueue_event(record)
        self.civic.commit()
        published = self._dispatch_event(event)

        logger.info(
            "record_processed",
            extra={
                "request_id": record.request_id,
                "category": record.category,
                "sub_category": record.sub_category,
                "data_quality_status": record.data_quality_status,
                "processing_status": record.processing_status,
                "issue_group_id": record.issue_group_id,
                "is_duplicate": record.is_duplicate,
            },
        )
        return self._outcome(record, event=event, event_published=published)

    def reprocess(self, request_id: str) -> ProcessingOutcome:
        """Re-run the pipeline for an existing record after processing logic changed."""
        bind_request_context(request_id, "reprocess")

        existing = self.civic.get_processed(request_id)
        if existing is None:
            raise ValueError(f"Processed record '{request_id}' not found")

        citizen = self.civic.get_citizen_request(request_id)
        if citizen is None:
            raise ValueError(f"Raw citizen request '{request_id}' not found; nothing to reprocess")

        self.civic.delete_processing_errors(request_id)

        cleaned = self.cleaning.clean(citizen.raw_payload or {})
        cleaned.request_id = request_id

        # The record's current group travels with the pipeline so that a
        # re-matched record is left untouched instead of double-counted.
        result = self._run_pipeline(
            cleaned,
            persist_errors=True,
            increment_location=False,
            preferred_group_id=existing.issue_group_id,
        )
        record = self._persist_record(cleaned, result, reprocessed=True)

        self.civic.set_citizen_request_status(cleaned.request_id, record.processing_status)
        event = self._enqueue_event(record)
        self.civic.commit()
        published = self._dispatch_event(event)

        logger.info("record_reprocessed", extra={"request_id": request_id, "reprocess_count": record.reprocess_count})
        return self._outcome(record, event=event, event_published=published)

    def get_record(self, request_id: str) -> ProcessedCivicRecord:
        record = self.civic.get_processed(request_id)
        if record is None:
            raise ValueError(f"Processed record '{request_id}' not found")
        return record

    def get_issue_group(self, issue_group_id: str) -> IssueGroup:
        group = self.issues.get(issue_group_id)
        if group is None:
            raise ValueError(f"Issue group '{issue_group_id}' not found")
        return group

    def get_records_for_issue(self, issue_group_id: str) -> list[ProcessedCivicRecord]:
        self.get_issue_group(issue_group_id)  # 404 when missing
        return list(self.civic.list_processed_by_issue_group(issue_group_id))

    # ------------------------------------------------------------------
    # pipeline
    # ------------------------------------------------------------------
    def _run_pipeline(
        self,
        cleaned: CleanedRecord,
        persist_errors: bool = True,
        increment_location: bool = True,
        preferred_group_id: Optional[str] = None,
    ) -> _PipelineResult:
        issues: list[dict[str, Any]] = list(cleaned.issue_dicts())

        # --- normalization (pure) -----------------------------------------
        set_stage("normalization")
        normalized = self.normalization.normalize(
            description=cleaned.description,
            category=cleaned.category_raw,
            sub_category=cleaned.sub_category_raw,
            language=cleaned.language,
        )

        # --- geocoding (never fatal) --------------------------------------
        set_stage("geocoding")
        geocode, geo_issues = self.geocoding.resolve(
            location_text=cleaned.location_text,
            latitude=cleaned.latitude,
            longitude=cleaned.longitude,
            district_hint=cleaned.district_hint,
            state_hint=cleaned.state_hint,
            country_hint=cleaned.country_hint,
        )
        issues.extend(geo_issues)

        # --- deduplication (never fatal) ----------------------------------
        set_stage("deduplication")
        assignment = self._assign_issue_group(cleaned, normalized, geocode, issues, preferred_group_id)

        # --- validation & quality -----------------------------------------
        set_stage("validation")
        validation = self.validation.validate(cleaned, normalized, geocode)
        quality = self.quality.score(cleaned, normalized, geocode, validation)

        # --- location persistence -----------------------------------------
        set_stage("persistence")
        location = self._safe(
            "persistence",
            lambda: self.civic.upsert_location(geocode, cleaned.location_text, increment=increment_location),
            fallback=None,
            issues=issues,
            request_id=cleaned.request_id,
        )

        if persist_errors:
            for issue in issues:
                self.civic.add_processing_error(
                    stage=issue.get("stage", "unknown"),
                    error_code=issue.get("code", "UNKNOWN"),
                    message=issue.get("message", ""),
                    request_id=cleaned.request_id,
                    severity=issue.get("severity", "warning"),
                    is_fatal=False,
                    details={"field": issue.get("field")},
                )
            for finding in validation.findings:
                self.civic.add_processing_error(
                    stage="validation",
                    error_code=finding.code,
                    message=finding.message,
                    request_id=cleaned.request_id,
                    severity=finding.severity,
                    is_fatal=finding.severity == "error",
                    details={"field": finding.field},
                )

        return _PipelineResult(
            cleaned=cleaned,
            normalized=normalized,
            geocode=geocode,
            validation=validation,
            quality_score=quality.score,
            quality_status=quality.status,
            quality_factors=quality.factors,
            assignment=assignment,
            location_id=location.id if location else None,
            issues=issues,
        )

    def _assign_issue_group(
        self,
        cleaned: CleanedRecord,
        normalized: NormalizationResult,
        geocode: GeocodeResult,
        issues: list[dict[str, Any]],
        preferred_group_id: Optional[str] = None,
    ) -> IssueAssignment:
        candidate = DeduplicationCandidate(
            request_id=cleaned.request_id,
            category=normalized.category,
            sub_category=normalized.sub_category,
            latitude=geocode.latitude if valid_coordinates(geocode.latitude, geocode.longitude) else None,
            longitude=geocode.longitude if valid_coordinates(geocode.latitude, geocode.longitude) else None,
            district=geocode.district,
            state=geocode.state,
            country=geocode.country,
            description=cleaned.description,
            severity=cleaned.severity,
            occurred_at=cleaned.created_at,
        )

        try:
            return self.deduplication.assign(candidate, preferred_group_id=preferred_group_id)
        except Exception as exc:  # duplicate detection must never break storage
            self.db.rollback()
            logger.error("deduplication_failed", extra={"request_id": cleaned.request_id, "error": str(exc)})
            issues.append(
                {
                    "stage": "deduplication",
                    "code": "DEDUPLICATION_FAILED",
                    "message": f"duplicate detection failed: {exc}",
                    "severity": "warning",
                    "field": None,
                }
            )
            return IssueAssignment(group=None, is_duplicate=False, skipped_reason=str(exc))

    def _safe(
        self,
        stage: str,
        action: Any,
        fallback: Any,
        issues: list[dict[str, Any]],
        request_id: Optional[str] = None,
    ) -> Any:
        """Run ``action`` swallowing non-fatal errors into the issue log."""
        try:
            return action()
        except Exception as exc:
            self.db.rollback()
            logger.warning(f"{stage}_stage_failed", extra={"request_id": request_id, "error": str(exc)})
            issues.append(
                {
                    "stage": stage,
                    "code": f"{stage.upper()}_FAILED",
                    "message": str(exc),
                    "severity": "warning",
                    "field": None,
                }
            )
            return fallback

    # ------------------------------------------------------------------
    # persistence helpers
    # ------------------------------------------------------------------
    def _persist_record(
        self,
        cleaned: CleanedRecord,
        result: _PipelineResult,
        reprocessed: bool,
    ) -> ProcessedCivicRecord:
        normalized = result.normalized
        geocode = result.geocode
        validation = result.validation

        if result.quality_status == DataQualityStatus.INVALID.value:
            processing_status = ProcessingStatus.REJECTED.value
        elif result.quality_status == DataQualityStatus.NEEDS_REVIEW.value:
            processing_status = ProcessingStatus.NEEDS_REVIEW.value
        else:
            processing_status = ProcessingStatus.PROCESSED.value

        # Invalid coordinates are never written to the spatial columns.
        if not valid_coordinates(cleaned.latitude, cleaned.longitude):
            latitude, longitude = None, None
        else:
            latitude, longitude = cleaned.latitude, cleaned.longitude

        fields: dict[str, Any] = {
            "category": normalized.category,
            "sub_category": normalized.sub_category,
            "description": cleaned.description,
            "language": cleaned.language,
            "severity": int(cleaned.severity) if cleaned.severity_valid else None,
            "location_id": result.location_id,
            "latitude": latitude,
            "longitude": longitude,
            "village": geocode.village,
            "ward": geocode.ward,
            "taluk": geocode.taluk,
            "district": geocode.district,
            "state": geocode.state,
            "country": geocode.country,
            "pincode": geocode.pincode,
            "location_status": geocode.location_status,
            "geocoding_provider": geocode.provider,
            "issue_group_id": result.assignment.issue_group_id,
            "is_duplicate": result.assignment.is_duplicate,
            "data_quality_score": result.quality_score,
            "data_quality_status": result.quality_status,
            "processing_status": processing_status,
            "normalization_confidence": normalized.confidence,
            "normalization_source": normalized.match_source,
            "matched_keywords": normalized.matched_keywords,
            "validation_findings": [finding.to_dict() for finding in validation.findings],
            "processing_issues": result.issues,
            "quality_factors": result.quality_factors,
            "processing_version": PROCESSING_VERSION,
            "payload_hash": cleaned.payload_hash,
            "raw_data": cleaned.raw_payload,
            "created_at": cleaned.created_at or utcnow(),
            "processed_at": utcnow(),
        }

        existing = self.civic.get_processed(cleaned.request_id)
        if existing is None:
            record = self.civic.create_processed(request_id=cleaned.request_id, **fields)
        else:
            record = self.civic.update_processed(
                existing,
                reprocess_count=(existing.reprocess_count or 0) + (1 if reprocessed else 0),
                **fields,
            )
        self.db.flush()
        return record

    def _enqueue_event(self, record: ProcessedCivicRecord) -> Optional[CivicEvent]:
        try:
            event = build_event_for_record(record)
            self.publisher.enqueue(event)
            return event
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("event_enqueue_failed", extra={"request_id": record.request_id, "error": str(exc)})
            return None

    def _dispatch_event(self, event: Optional[CivicEvent]) -> bool:
        if event is None:
            return False
        return self.publisher.dispatch(event.event_id)

    def _outcome(
        self,
        record: ProcessedCivicRecord,
        idempotent: bool = False,
        payload_changed: bool = False,
        event: Optional[CivicEvent] = None,
        event_published: bool = False,
    ) -> ProcessingOutcome:
        issues: list[dict[str, Any]] = list(record.processing_issues or [])
        issues.extend(record.validation_findings or [])
        return ProcessingOutcome(
            record=record,
            idempotent=idempotent,
            payload_changed=payload_changed,
            event=event,
            event_published=event_published,
            issues=issues,
        )
