"""Processing errors.

Every non-fatal problem found while cleaning, normalizing, geocoding,
deduplicating or validating a record is persisted here (in addition to being
embedded in the record itself) so that operators can query failures across the
whole pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utcnow
from app.database.base import Base


class ProcessingError(Base):
    __tablename__ = "processing_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    error_code: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="error", index=True)
    is_fatal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<ProcessingError {self.stage}:{self.error_code} request={self.request_id}>"
