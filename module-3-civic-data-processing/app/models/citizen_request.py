"""Raw citizen request received from Module 2 (originally Module 1).

The raw record is immutable: cleaning never overwrites it. This guarantees that
the original citizen submission is always recoverable, even if a later
processing stage is changed or fails.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.database.base import Base

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.civic_record import ProcessedCivicRecord


class CitizenRequest(Base):
    __tablename__ = "citizen_requests"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_module: Mapped[str] = mapped_column(String(32), nullable=False, default="module-2")
    channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # --- raw, untouched values -------------------------------------------
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    raw_sub_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    raw_severity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_location_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    raw_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RECEIVED")

    processed_record: Mapped[Optional["ProcessedCivicRecord"]] = relationship(
        back_populates="citizen_request",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<CitizenRequest {self.request_id} status={self.status}>"
