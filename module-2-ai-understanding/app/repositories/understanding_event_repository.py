"""Persistence for :class:`UnderstandingEvent` -- the Module 2 outbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.utils import utcnow
from app.models.understanding_event import UnderstandingEvent


class UnderstandingEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        event_id: str,
        request_id: str,
        event_type: str,
        channel: str,
        payload: dict[str, Any],
        source_event_id: Optional[str] = None,
        source_channel: Optional[str] = None,
    ) -> UnderstandingEvent:
        record = UnderstandingEvent(
            event_id=event_id,
            request_id=request_id,
            event_type=event_type,
            channel=channel,
            payload=payload,
            source_event_id=source_event_id,
            source_channel=source_channel,
            status="PENDING",
        )
        self.session.add(record)
        return record

    def get(self, event_id: str) -> Optional[UnderstandingEvent]:
        return self.session.get(UnderstandingEvent, event_id)

    def get_by_request_id(self, request_id: str) -> Optional[UnderstandingEvent]:
        return self.session.scalar(
            select(UnderstandingEvent).where(UnderstandingEvent.request_id == request_id)
        )

    def list_pending(self, limit: int = 100) -> list[UnderstandingEvent]:
        return list(
            self.session.scalars(
                select(UnderstandingEvent)
                .where(UnderstandingEvent.status.in_(("PENDING", "FAILED")))
                .order_by(UnderstandingEvent.created_at)
                .limit(limit)
            ).all()
        )

    def mark_published(self, event_id: str) -> None:
        record = self.get(event_id)
        if record is None:
            return
        record.status = "PUBLISHED"
        record.attempts += 1
        record.last_error = None
        record.published_at = utcnow()

    def mark_failed(self, event_id: str, error: str) -> None:
        record = self.get(event_id)
        if record is None:
            return
        record.status = "FAILED"
        record.attempts += 1
        record.last_error = error[:2000]

    def count_by_status(self) -> dict[str, int]:
        rows = self.session.execute(
            select(UnderstandingEvent.status, func.count(UnderstandingEvent.event_id)).group_by(
                UnderstandingEvent.status
            )
        ).all()
        return {str(status): int(count) for status, count in rows}

    def set_published_at(self, event_id: str, when: Optional[datetime] = None) -> None:
        record = self.get(event_id)
        if record is not None:
            record.published_at = when or utcnow()
