"""Durable ledger for the Module 2 -> Module 3 event.

Two jobs, one table:

* **Outbox** - the completion event is written in the same transaction as the
  understanding record. If Redis is down the row stays ``PENDING``/``FAILED``
  and is retried later, so an understood citizen request is never silently
  dropped before Module 3 hears about it.
* **Idempotency ledger** - ``request_id`` is unique here, so a redelivered
  upstream event cannot produce a second downstream event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utcnow
from app.database.base import Base


class UnderstandingEvent(Base):
    """One ``UNDERSTANDING_COMPLETED`` event, tracked from enqueue to delivery."""

    __tablename__ = "understanding_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Provenance: which Module 1 event triggered this understanding, so the
    # whole chain can be walked backwards from Module 4.
    source_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_channel: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<UnderstandingEvent {self.event_id} {self.request_id} {self.status}>"
