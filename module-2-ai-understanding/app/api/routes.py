"""HTTP routes for Module 2 - AI Understanding."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    BatchUnderstandIn,
    BatchUnderstandOut,
    CapabilitiesOut,
    CategoryBreakdownOut,
    HealthOut,
    LanguageBreakdownOut,
    RecordListOut,
    RecordOut,
    StageCapability,
    TextUnderstandIn,
    UnderstandingOut,
)
from app.core.config import settings
from app.core.enums import UnderstandingStatus
from app.core.logging import get_logger
from app.database.connection import get_db
from app.models.understanding_record import UnderstandingRecord
from app.repositories.understanding_repository import UnderstandingRepository
from app.services import (
    asr_service,
    classification_service,
    image_service,
    ner_service,
    translation_service,
    understanding_service,
)

logger = get_logger(__name__)

health_router = APIRouter(tags=["health"])
understand_router = APIRouter(prefix="/understand", tags=["understand"])
analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])


@health_router.get("/health", response_model=HealthOut, summary="Service health check")
def health_check(db: Session = Depends(get_db)) -> HealthOut:
    try:
        count = db.scalar(select(func.count(UnderstandingRecord.id))) or 0
        by_status = UnderstandingRepository(db).count_by_status()
        ready = True
    except Exception as exc:  # pragma: no cover - surfaced as unhealthy
        logger.warning("health_check_failed", extra={"error": str(exc)})
        count, by_status, ready = 0, {}, False

    return HealthOut(
        status="healthy" if ready else "degraded",
        service=settings.service_name,
        version=settings.version,
        database_ready=ready,
        records=int(count),
        by_status=by_status,
    )


@health_router.get(
    "/capabilities",
    response_model=CapabilitiesOut,
    summary="Which AI capabilities are active in this process",
)
def capabilities() -> CapabilitiesOut:
    """Report live AI capabilities.

    Optional open-weight models (Whisper ASR, NLLB translation, CLIP image
    labels) are detected at import time. Everything else runs offline with
    deterministic rules, so the service is fully functional with zero model
    downloads -- this endpoint simply makes that state explicit.
    """
    return CapabilitiesOut(
        service=settings.service_name,
        version=settings.version,
        offline_capable=True,
        stages=[
            StageCapability(**asr_service.capabilities()),
            StageCapability(**translation_service.capabilities()),
            StageCapability(**ner_service.capabilities()),
            StageCapability(**classification_service.capabilities()),
            StageCapability(**image_service.capabilities()),
        ],
    )


# --- understanding -----------------------------------------------------------
def _record_to_out(record: UnderstandingRecord) -> RecordOut:
    return RecordOut(
        id=record.id,
        request_id=record.request_id,
        media_type=record.media_type,
        status=record.status,
        source_channel=record.source_channel,
        detected_language=record.detected_language,
        language_confidence=record.language_confidence,
        original_text=record.original_text,
        transcript=record.transcript,
        english_text=record.english_text,
        translation_provider=record.translation_provider,
        translation_is_gloss=bool(record.translation_is_gloss),
        category=record.category,
        sub_category=record.sub_category,
        category_confidence=record.category_confidence,
        matched_keywords=record.matched_keywords or [],
        severity=record.severity,
        severity_band=record.severity_band,
        severity_confidence=record.severity_confidence,
        entities=record.entities or [],
        image_analysis=record.image_analysis,
        hint_latitude=record.hint_latitude,
        hint_longitude=record.hint_longitude,
        stage_status=record.stage_status or {},
        warnings=record.warnings or [],
        pipeline_version=record.pipeline_version,
        processing_ms=record.processing_ms,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_and_store(
    repo: UnderstandingRepository,
    request_id: str,
    text: Optional[str],
    audio: Optional[bytes],
    image: Optional[bytes],
    source_channel: str,
    language_hint: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
) -> RecordOut:
    """Run the pipeline, persist it, and return the stored record."""
    started = time.perf_counter()
    try:
        result = understanding_service.understand(
            request_id=request_id,
            text=text,
            audio_bytes=audio,
            image_bytes=image,
            hint_language=language_hint,
            hint_latitude=latitude,
            hint_longitude=longitude,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    try:
        record = repo.save(result, source_channel=source_channel, processing_ms=elapsed_ms)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return _record_to_out(record)


@understand_router.post(
    "/text",
    response_model=RecordOut,
    status_code=status.HTTP_201_CREATED,
    summary="Understand a text request (multilingual)",
)
def understand_text(
    payload: TextUnderstandIn, db: Session = Depends(get_db)
) -> RecordOut:
    """Run the pipeline on citizen text and store the result."""
    return _run_and_store(
        repo=UnderstandingRepository(db),
        request_id=payload.request_id,
        text=payload.text,
        audio=None,
        image=None,
        source_channel=payload.source_channel,
        language_hint=payload.language_hint,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )


@understand_router.post(
    "/batch",
    response_model=BatchUnderstandOut,
    status_code=status.HTTP_201_CREATED,
    summary="Understand a batch of text requests",
)
def understand_batch(
    payload: BatchUnderstandIn, db: Session = Depends(get_db)
) -> BatchUnderstandOut:
    """Bulk-process a backlog of already-ingested text requests.

    One failed record does not abort the batch: each item is reported
    individually so the caller can retry only what failed.
    """
    repo = UnderstandingRepository(db)
    results: list[RecordOut] = []
    counts = {state.value: 0 for state in UnderstandingStatus}
    failures: list[dict] = []

    for item in payload.requests:
        try:
            record = _run_and_store(
                repo=repo,
                request_id=item.request_id,
                text=item.text,
                audio=None,
                image=None,
                source_channel=item.source_channel,
                language_hint=item.language_hint,
                latitude=item.latitude,
                longitude=item.longitude,
            )
        except HTTPException as exc:
            failures.append({"request_id": item.request_id, "detail": exc.detail})
            continue
        counts[record.status] = counts.get(record.status, 0) + 1
        results.append(record)

    if failures:
        logger.warning("batch_partial_failure", extra={"failed": len(failures)})

    return BatchUnderstandOut(
        total=len(payload.requests),
        understood=counts.get(UnderstandingStatus.UNDERSTOOD.value, 0),
        partial=counts.get(UnderstandingStatus.PARTIAL.value, 0),
        failed=counts.get(UnderstandingStatus.FAILED.value, 0) + len(failures),
        results=results,
    )


@understand_router.post(
    "/audio",
    response_model=RecordOut,
    status_code=status.HTTP_201_CREATED,
    summary="Understand a voice note (ASR + NLP)",
)
def understand_audio(
    request_id: str = Query(..., min_length=3, max_length=64),
    source_channel: str = Query("IVR"),
    language_hint: Optional[str] = Query(None, max_length=20),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RecordOut:
    """Transcribe a voice note, then run the text pipeline over it.

    When no ASR provider is installed the stage is reported as ``SKIPPED`` and
    the record is still created, so the voice note is never silently lost.
    """
    payload = read_upload(audio)
    return _run_and_store(
        repo=UnderstandingRepository(db),
        request_id=request_id,
        text=None,
        audio=payload,
        image=None,
        source_channel=source_channel,
        language_hint=language_hint,
        latitude=None,
        longitude=None,
    )


@understand_router.post(
    "/image",
    response_model=RecordOut,
    status_code=status.HTTP_201_CREATED,
    summary="Understand a citizen photo (quality + optional labels)",
)
def understand_image(
    request_id: str = Query(..., min_length=3, max_length=64),
    text: Optional[str] = Query(None, max_length=5_000),
    source_channel: str = Query("WEB"),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RecordOut:
    """Measure image quality and, when a model is installed, label it.

    A citizen can send a photo with a short caption; the caption goes through
    the text stages so the image and the description are cross-checked.
    """
    payload = read_upload(image)
    return _run_and_store(
        repo=UnderstandingRepository(db),
        request_id=request_id,
        text=text,
        audio=None,
        image=payload,
        source_channel=source_channel,
        language_hint=None,
        latitude=None,
        longitude=None,
    )


@understand_router.post(
    "/rerun/{request_id}",
    response_model=RecordOut,
    summary="Re-run the pipeline for an already-understood request",
)
def rerun_request(
    request_id: str,
    text: Optional[str] = Query(None, max_length=5_000),
    db: Session = Depends(get_db),
) -> RecordOut:
    """Recompute a stored result, e.g. after installing a better model.

    Explicit by design: Module 3 has already consumed the previous label, so a
    silent re-label would create an inconsistency nobody could audit.
    """
    repo = UnderstandingRepository(db)
    existing = repo.get_by_request_id(request_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no understanding record for request {request_id}",
        )

    source_text = text or existing.original_text or existing.english_text or ""
    if not source_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="record has no text to re-run and no replacement text was supplied",
        )

    started = time.perf_counter()
    result = understanding_service.understand(request_id=request_id, text=source_text)
    record = repo.rerun(
        request_id, result, processing_ms=int((time.perf_counter() - started) * 1000)
    )
    return _record_to_out(record)


@understand_router.get("", response_model=RecordListOut, summary="List stored understandings")
def list_understandings(
    category: Optional[str] = Query(None, max_length=40),
    language: Optional[str] = Query(None, max_length=20),
    status_filter: Optional[str] = Query(None, alias="status", max_length=20),
    min_severity: Optional[int] = Query(None, ge=1, le=5),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> RecordListOut:
    """Filterable, newest-first listing used by the review dashboard."""
    repo = UnderstandingRepository(db)
    records = repo.list_records(
        category=category,
        language=language,
        status=status_filter,
        min_severity=min_severity,
        limit=limit,
        offset=offset,
    )
    return RecordListOut(
        total=len(records),
        limit=limit,
        offset=offset,
        items=[_record_to_out(record) for record in records],
    )


@understand_router.get(
    "/{request_id}", response_model=RecordOut, summary="Get a stored understanding"
)
def get_understanding(
    request_id: str, db: Session = Depends(get_db)
) -> RecordOut:
    repo = UnderstandingRepository(db)
    record = repo.get_by_request_id(request_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no understanding record for request {request_id}",
        )
    return _record_to_out(record)


# --- analytics ---------------------------------------------------------------
@analytics_router.get(
    "/categories",
    response_model=list[CategoryBreakdownOut],
    summary="Complaint volume and severity by category",
)
def category_breakdown(db: Session = Depends(get_db)) -> list[CategoryBreakdownOut]:
    """Volume/severity summary consumed by Module 4 for enrichment priority."""
    return [
        CategoryBreakdownOut(**row)
        for row in UnderstandingRepository(db).category_breakdown()
    ]


@analytics_router.get(
    "/languages",
    response_model=list[LanguageBreakdownOut],
    summary="Record count per detected language (multilingual reach)",
)
def language_breakdown(db: Session = Depends(get_db)) -> list[LanguageBreakdownOut]:
    return [
        LanguageBreakdownOut(**row) for row in UnderstandingRepository(db).language_breakdown()
    ]


def read_upload(upload: UploadFile) -> bytes:
    """Read an uploaded file, enforcing the size limit for its modality.

    ``UploadFile.file`` is a spooled file object, so reading it is a cheap
    synchronous call and this helper works from a sync route handler.
    """
    content_type = (upload.content_type or "").lower()
    if content_type.startswith("audio/"):
        limit = settings.max_audio_bytes
    elif content_type.startswith("image/"):
        limit = settings.max_image_bytes
    else:
        limit = settings.max_upload_bytes

    payload = upload.file.read()
    if len(payload) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty"
        )
    if len(payload) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"upload exceeds the {limit} byte limit for this media type",
        )
    return payload
