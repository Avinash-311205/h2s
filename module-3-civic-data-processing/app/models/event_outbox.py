"""Transactional outbox for downstream (Module 4) events.

When Redis is unavailable the processed record is still persisted and the event
stays ``PENDING`` here, so a later retry can deliver it. This is the mechanism
that keeps ``CIVIC_RECORD_PROCESSED`` from being lost.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utcnow
from app.database.base import Base


class EventOutbox(Base):
    __tablename__ = "event_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(120), nullable=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    issue_group_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<EventOutbox {self.event_type} status={self.status} attempts={self.attempts}>"
