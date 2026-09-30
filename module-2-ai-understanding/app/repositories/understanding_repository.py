"""Repository for :class:`UnderstandingRecord` -- the only place issuing SQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import UnderstandingStatus
from app.core.utils import utcnow
from app.models.understanding_record import UnderstandingRecord
from app.services.understanding_service import Understanding


class UnderstandingRepository:
    """Persistence operations for understanding results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(
        self,
        result: Understanding,
        source_channel: str = "UNKNOWN",
        processing_ms: int = 0,
    ) -> UnderstandingRecord:
        """Insert a new understanding result.

        Raises:
            ValueError: if the ``request_id`` was already understood. Re-running
                is an explicit operation (``rerun``), never an implicit upsert,
                so downstream modules never see a silently changed label.
        """
        if self.get_by_request_id(result.request_id) is not None:
            raise ValueError(
                f"request {result.request_id} already has an understanding record; "
                "use POST /understand/rerun to recompute it."
            )

        record = UnderstandingRecord(
            request_id=result.request_id,
            source_channel=source_channel,
            media_type=result.media_type,
            detected_language=result.detected_language,
            language_confidence=result.language_confidence,
            original_text=result.original_text or None,
            transcript=result.transcript,
            english_text=result.english_text or None,
            translation_provider=result.translation_provider,
            translation_is_gloss=result.translation_is_gloss,
            category=result.category,
            sub_category=result.sub_category,
            category_confidence=result.category_confidence,
            matched_keywords=result.matched_keywords,
            severity=result.severity,
            severity_band=result.severity_band,
            severity_confidence=result.severity_confidence,
            entities=result.entities,
            image_analysis=result.image_analysis,
            hint_latitude=result.raw_payload.get("hint_latitude"),
            hint_longitude=result.raw_payload.get("hint_longitude"),
            stage_status=result.stage_status,
            status=result.status,
            warnings=result.warnings,
            raw_payload=result.raw_payload,
            pipeline_version=settings.version,
            processing_ms=processing_ms,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def rerun(
        self, request_id: str, result: Understanding, processing_ms: int = 0
    ) -> UnderstandingRecord:
        """Replace the stored result for a request after re-running the pipeline."""
        record = self.get_by_request_id(request_id)
        if record is None:
            raise ValueError(f"no understanding record for request {request_id}")

        record.detected_language = result.detected_language
        record.language_confidence = result.language_confidence
        record.original_text = result.original_text or None
        record.transcript = result.transcript
        record.english_text = result.english_text or None
        record.translation_provider = result.translation_provider
        record.translation_is_gloss = result.translation_is_gloss
        record.category = result.category
        record.sub_category = result.sub_category
        record.category_confidence = result.category_confidence
        record.matched_keywords = result.matched_keywords
        record.severity = result.severity
        record.severity_band = result.severity_band
        record.severity_confidence = result.severity_confidence
        record.entities = result.entities
        record.image_analysis = result.image_analysis
        record.stage_status = result.stage_status
        record.status = result.status
        record.warnings = result.warnings
        record.processing_ms = processing_ms
        record.updated_at = utcnow()

        self.session.commit()
        self.session.refresh(record)
        return record

    def get_by_request_id(self, request_id: str) -> Optional[UnderstandingRecord]:
        return self.session.scalar(
            select(UnderstandingRecord).where(UnderstandingRecord.request_id == request_id)
        )

    def list_records(
        self,
        category: Optional[str] = None,
        language: Optional[str] = None,
        status: Optional[str] = None,
        min_severity: Optional[int] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[UnderstandingRecord]:
        """Filterable, newest-first listing of understanding results."""
        statement = select(UnderstandingRecord)
        if category:
            statement = statement.where(UnderstandingRecord.category == category)
        if language:
            statement = statement.where(UnderstandingRecord.detected_language == language)
        if status:
            statement = statement.where(UnderstandingRecord.status == status)
        if min_severity is not None:
            statement = statement.where(UnderstandingRecord.severity >= min_severity)
        if since is not None:
            statement = statement.where(UnderstandingRecord.created_at >= since)

        statement = statement.order_by(UnderstandingRecord.created_at.desc())
        if limit:
            statement = statement.limit(limit).offset(offset)
        return list(self.session.scalars(statement).all())

    def category_breakdown(self) -> list[dict[str, Any]]:
        """Count records per category, ordered by volume.

        Feeds the "what are citizens complaining about" summary used by
        Module 4/5 to prioritise which reference data to enrich first.
        """
        rows = self.session.execute(
            select(
                UnderstandingRecord.category,
                func.count(UnderstandingRecord.id),
                func.avg(UnderstandingRecord.severity),
                func.avg(UnderstandingRecord.category_confidence),
            )
            .group_by(UnderstandingRecord.category)
            .order_by(func.count(UnderstandingRecord.id).desc())
        ).all()

        return [
            {
                "category": row[0],
                "count": int(row[1]),
                "avg_severity": round(float(row[2]), 2) if row[2] is not None else None,
                "avg_confidence": round(float(row[3]), 3) if row[3] is not None else None,
            }
            for row in rows
        ]

    def language_breakdown(self) -> list[dict[str, Any]]:
        """Count records per detected language -- the multilingual reach metric."""
        rows = self.session.execute(
            select(UnderstandingRecord.detected_language, func.count(UnderstandingRecord.id))
            .group_by(UnderstandingRecord.detected_language)
            .order_by(func.count(UnderstandingRecord.id).desc())
        ).all()
        return [{"language": row[0], "count": int(row[1])} for row in rows]

    def count_by_status(self) -> dict[str, int]:
        """Record counts per understanding status, for the health endpoint."""
        rows = self.session.execute(
            select(UnderstandingRecord.status, func.count(UnderstandingRecord.id)).group_by(
                UnderstandingRecord.status
            )
        ).all()
        counts = {status.value: 0 for status in UnderstandingStatus}
        for status_value, count in rows:
            counts[status_value] = int(count)
        return counts
