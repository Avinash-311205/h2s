"""Transactional outbox repository for downstream events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import EventStatus
from app.core.logging import get_logger
from app.core.utils import utcnow
from app.models.event_outbox import EventOutbox

logger = get_logger(__name__)


class EventOutboxRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(
        self,
        event_id: str,
        event_type: str,
        channel: str,
        payload: dict[str, Any],
        request_id: Optional[str] = None,
        issue_group_id: Optional[str] = None,
    ) -> EventOutbox:
        event = EventOutbox(
            event_id=event_id,
            event_type=event_type,
            channel=channel,
            request_id=request_id,
            issue_group_id=issue_group_id,
            payload=payload,
            status=EventStatus.PENDING.value,
            attempts=0,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def get(self, event_id: str) -> Optional[EventOutbox]:
        return self.db.query(EventOutbox).filter(EventOutbox.event_id == event_id).first()

    def mark_published(self, event_id: str) -> None:
        event = self.get(event_id)
        if event is None:
            return
        event.status = EventStatus.PUBLISHED.value
        event.published_at = utcnow()
        event.last_error = None
        self.db.flush()

    def mark_failed(self, event_id: str, error: str, retry_at: Optional[datetime] = None) -> None:
        event = self.get(event_id)
        if event is None:
            return
        event.status = EventStatus.FAILED.value
        event.attempts = (event.attempts or 0) + 1
        event.last_error = error[:2000]
        event.next_retry_at = retry_at or utcnow()
        self.db.flush()

    def register_attempt(self, event_id: str) -> None:
        event = self.get(event_id)
        if event is None:
            return
        event.attempts = (event.attempts or 0) + 1
        self.db.flush()

    def list_pending(self, limit: int = 100) -> Sequence[EventOutbox]:
        return (
            self.db.query(EventOutbox)
            .filter(EventOutbox.status.in_([EventStatus.PENDING.value, EventStatus.FAILED.value]))
            .order_by(EventOutbox.created_at.asc())
            .limit(limit)
            .all()
        )

    def list_by_request(self, request_id: str) -> Sequence[EventOutbox]:
        return (
            self.db.query(EventOutbox)
            .filter(EventOutbox.request_id == request_id)
            .order_by(EventOutbox.created_at.asc())
            .all()
        )

    def stats(self) -> dict[str, int]:
        rows = (
            self.db.query(EventOutbox.status, func.count(EventOutbox.id))
            .group_by(EventOutbox.status)
            .all()
        )
        return {status: count for status, count in rows}
