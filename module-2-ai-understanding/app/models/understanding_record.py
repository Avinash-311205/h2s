"""SQLAlchemy model for a stored understanding result."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utcnow
from app.database.base import Base


class UnderstandingRecord(Base):
    """One citizen request after the Module 2 understanding pipeline.

    Stores the inferred fields *and* the raw input plus per-stage status, so
    any inference can be re-examined (and re-run with a better model) later
    without re-ingesting from the citizen.
    """

    __tablename__ = "understanding_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Upstream (Module 1) linkage.
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    source_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")

    media_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")

    # --- language ----------------------------------------------------------
    detected_language: Mapped[str] = mapped_column(String(20), nullable=False, default="und", index=True)
    language_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- text --------------------------------------------------------------
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    english_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translation_provider: Mapped[str] = mapped_column(String(30), nullable=False, default="passthrough")
    translation_is_gloss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- classification ----------------------------------------------------
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN", index=True)
    sub_category: Mapped[str] = mapped_column(String(60), nullable=False, default="UNCLASSIFIED", index=True)
    category_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    matched_keywords: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)

    # --- severity ----------------------------------------------------------
    severity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    severity_band: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW")
    severity_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- extracted evidence ------------------------------------------------
    entities: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    image_analysis: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # GPS hint from the handset: handed to Module 3's geocoder, never inferred.
    hint_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hint_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # --- audit / telemetry -------------------------------------------------
    stage_status: Mapped[Optional[dict[str, str]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNDERSTOOD", index=True)
    warnings: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pipeline_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    processing_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_understanding_category_sub", "category", "sub_category"),
        Index("ix_understanding_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<UnderstandingRecord {self.request_id} {self.category}/{self.sub_category} "
            f"sev={self.severity} lang={self.detected_language}>"
        )
