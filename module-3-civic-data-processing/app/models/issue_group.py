"""Issue groups.

Duplicate citizen reports are never deleted. Instead each processed record is
linked to an ``IssueGroup`` that represents the underlying civic issue, and the
group carries the aggregate ``report_count`` used downstream.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.database.base import Base

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.civic_record import ProcessedCivicRecord


class IssueGroup(Base):
    __tablename__ = "issue_groups"

    issue_group_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    sub_category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    state: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN", index=True)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severity_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    severity_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    first_reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    representative_request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    representative_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    records: Mapped[list["ProcessedCivicRecord"]] = relationship(back_populates="issue_group")

    __table_args__ = (
        Index("ix_issue_groups_category_sub_category", "category", "sub_category"),
        Index("ix_issue_groups_bbox", "latitude", "longitude"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<IssueGroup {self.issue_group_id} {self.category}/{self.sub_category} "
            f"reports={self.report_count}>"
        )
