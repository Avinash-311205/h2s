"""The standard civic record handed to Module 4 and beyond."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.database.base import Base

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.citizen_request import CitizenRequest
    from app.models.issue_group import IssueGroup
    from app.models.location import Location


class ProcessedCivicRecord(Base):
    __tablename__ = "processed_civic_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("citizen_requests.request_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    sub_category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    severity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- denormalized geography (indexed for national-scale filtering) -----
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True
    )
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    village: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    ward: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    taluk: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    state: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    location_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNRESOLVED")
    geocoding_provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # --- issue grouping ----------------------------------------------------
    issue_group_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("issue_groups.issue_group_id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- quality / processing telemetry -----------------------------------
    data_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    data_quality_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NEEDS_REVIEW")
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PROCESSED")
    normalization_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    normalization_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    matched_keywords: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    validation_findings: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    # Stage-tagged issue log from cleaning, geocoding and deduplication.
    processing_issues: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    quality_factors: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    processing_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    reprocess_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # ``raw_data`` keeps the incoming Module 2 payload next to the processed
    # output so the transformation is always auditable.
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    citizen_request: Mapped["CitizenRequest"] = relationship(back_populates="processed_record")
    issue_group: Mapped[Optional["IssueGroup"]] = relationship(back_populates="records")
    location: Mapped[Optional["Location"]] = relationship(back_populates="records")

    __table_args__ = (
        Index("ix_processed_records_category_sub_category", "category", "sub_category"),
        Index("ix_processed_records_state_district", "state", "district"),
        Index("ix_processed_records_bbox", "latitude", "longitude"),
        Index("ix_processed_records_quality_status", "data_quality_status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<ProcessedCivicRecord {self.request_id} {self.category}/{self.sub_category} "
            f"quality={self.data_quality_status}>"
        )
