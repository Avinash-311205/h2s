"""Standardized location entity produced by the geocoding layer."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.database.base import Base

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.civic_record import ProcessedCivicRecord


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ``location_key`` de-duplicates repeated resolutions of the same point.
    location_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    # Portable WKT copy of the point. A native PostGIS ``geom`` column is added
    # by ``app.database.postgis`` when PostGIS is enabled.
    geom_wkt: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    village: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    ward: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    taluk: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    state: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    location_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    location_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNRESOLVED")
    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    records: Mapped[list["ProcessedCivicRecord"]] = relationship(back_populates="location")

    __table_args__ = (
        Index("ix_locations_admin_hierarchy", "state", "district", "taluk"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Location {self.id} {self.district}/{self.state} status={self.location_status}>"
