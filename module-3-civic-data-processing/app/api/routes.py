"""REST API routes.

Thin controllers only: validate input, delegate to :class:`CivicProcessingService`
or a repository, translate domain errors into HTTP responses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    CivicRecordOut,
    EventRetryOut,
    HealthOut,
    IssueGroupOut,
    Module2RecordIn,
    ProcessingIssueOut,
    ProcessResponse,
    ProcessingStatusOut,
    civic_record_out,
    event_out,
    issue_group_out,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.database.connection import get_db
from app.repositories.event_repository import EventOutboxRepository
from app.services.health_service import HealthService
from app.services.processing_service import CivicProcessingService, ProcessingFailure

logger = get_logger(__name__)

health_router = APIRouter(tags=["health"])
civic_router = APIRouter(prefix="/civic", tags=["civic"])
issues_router = APIRouter(prefix="/issues", tags=["issues"])
events_router = APIRouter(prefix="/events", tags=["events"])


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@health_router.get("/health", response_model=HealthOut, summary="Service health check")
def health_check(db: Session = Depends(get_db)) -> HealthOut:
    payload = HealthService(db).check()
    return HealthOut(**payload)


# ---------------------------------------------------------------------------
# civic records
# ---------------------------------------------------------------------------
@civic_router.post(
    "/process",
    response_model=ProcessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Process a Module 2 record into a standardized civic record",
)
def process_record(
    payload: Module2RecordIn,
    response: Response,
    db: Session = Depends(get_db),
) -> ProcessResponse:
    service = CivicProcessingService(db)
    try:
        outcome = service.process(payload.model_dump(exclude_none=True))
    except ProcessingFailure as exc:
        logger.error("processing_failed", extra={"request_id": exc.request_id, "stage": exc.stage})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": str(exc),
                "request_id": exc.request_id,
                "stage": exc.stage,
                "hint": "The raw citizen request was preserved; retry or use the reprocess endpoint.",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if outcome.idempotent:
        response.status_code = status.HTTP_200_OK

    return ProcessResponse(
        idempotent=outcome.idempotent,
        payload_changed=outcome.payload_changed,
        event_published=outcome.event_published,
        event_type=outcome.event.event_type if outcome.event else None,
        errors=[ProcessingIssueOut(**issue) for issue in outcome.issues],
        record=civic_record_out(outcome.record),
    )


@civic_router.post(
    "/reprocess/{request_id}",
    response_model=ProcessResponse,
    summary="Reprocess an existing record after processing logic changes",
)
def reprocess_record(request_id: str, db: Session = Depends(get_db)) -> ProcessResponse:
    service = CivicProcessingService(db)
    try:
        outcome = service.reprocess(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProcessingFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": str(exc), "request_id": exc.request_id, "stage": exc.stage},
        ) from exc

    return ProcessResponse(
        idempotent=False,
        payload_changed=False,
        event_published=outcome.event_published,
        event_type=outcome.event.event_type if outcome.event else None,
        errors=[ProcessingIssueOut(**issue) for issue in outcome.issues],
        record=civic_record_out(outcome.record),
    )


@civic_router.get("/{request_id}", response_model=CivicRecordOut, summary="Get a processed civic record")
def get_record(request_id: str, db: Session = Depends(get_db)) -> CivicRecordOut:
    service = CivicProcessingService(db)
    try:
        return civic_record_out(service.get_record(request_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@civic_router.get(
    "/{request_id}/status",
    response_model=ProcessingStatusOut,
    summary="Get processing status, quality and event delivery for a request",
)
def get_processing_status(request_id: str, db: Session = Depends(get_db)) -> ProcessingStatusOut:
    service = CivicProcessingService(db)
    try:
        record = service.get_record(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    events = EventOutboxRepository(db).list_by_request(request_id)
    issues = [ProcessingIssueOut(**issue) for issue in (record.processing_issues or [])]
    issues.extend(ProcessingIssueOut(**finding) for finding in (record.validation_findings or []))

    return ProcessingStatusOut(
        request_id=record.request_id,
        processing_status=record.processing_status,
        data_quality_status=record.data_quality_status,
        data_quality_score=record.data_quality_score,
        issue_group_id=record.issue_group_id,
        is_duplicate=bool(record.is_duplicate),
        processed_at=record.processed_at,
        reprocess_count=record.reprocess_count or 0,
        errors=issues,
        events=[event_out(event) for event in events],
    )


# ---------------------------------------------------------------------------
# issue groups
# ---------------------------------------------------------------------------
@issues_router.get("/{issue_group_id}", response_model=IssueGroupOut, summary="Get an issue group")
def get_issue_group(issue_group_id: str, db: Session = Depends(get_db)) -> IssueGroupOut:
    service = CivicProcessingService(db)
    try:
        return issue_group_out(service.get_issue_group(issue_group_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@issues_router.get(
    "/{issue_group_id}/requests",
    response_model=list[CivicRecordOut],
    summary="List every citizen request linked to an issue group",
)
def get_issue_group_requests(
    issue_group_id: str,
    limit: int = Query(default=None, ge=1, le=settings.max_page_size),
    db: Session = Depends(get_db),
) -> list[CivicRecordOut]:
    service = CivicProcessingService(db)
    try:
        records = service.get_records_for_issue(issue_group_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    effective_limit = limit or settings.default_page_size
    return [civic_record_out(record) for record in records[:effective_limit]]


# ---------------------------------------------------------------------------
# internal: event outbox
# ---------------------------------------------------------------------------
@events_router.post("/retry", response_model=EventRetryOut, summary="Retry undelivered downstream events")
def retry_events(db: Session = Depends(get_db)) -> EventRetryOut:
    from app.events.publisher import publisher_for_session

    repository = EventOutboxRepository(db)
    before = repository.stats()
    summary = publisher_for_session(db).retry_pending()
    return EventRetryOut(**summary, pending_before=before)


@events_router.get("/pending", summary="List undelivered downstream events")
def list_pending_events(
    limit: int = Query(default=None, ge=1, le=settings.event_outbox_batch_size),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    repository = EventOutboxRepository(db)
    pending = repository.list_pending(limit or settings.event_outbox_batch_size)
    return {
        "count": len(pending),
        "stats": repository.stats(),
        "events": [event_out(event).model_dump() for event in pending],
    }
