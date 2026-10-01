"""Tables owned by Module 6.

Three tables, and the split is deliberate. ``priority_scores`` holds only the
current ranking and is *replaced* wholesale on each run, so reads are simple and
never see a half-finished recompute. ``priority_history`` is append-only and
keeps the previous ranking, which is the only way to answer "is this getting
worse" over time. ``priority_runs`` records what each run attempted and whether
it succeeded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PriorityBand, RunStatus
from app.core.utils import utcnow
from app.database.base import Base


class PriorityScore(Base):
    """One hotspot's current priority score and the evidence behind it.

    ``evidence`` and ``factors`` are JSON rather than columns because they are a
    description of a calculation, not something queried or aggregated on. The
    score itself is stored separately precisely so the summary queries stay cheap
    even as the evidence grows.
    """

    __tablename__ = "priority_scores"
    __table_args__ = (
        Index("ix_priority_band", "band"),
        Index("ix_priority_district", "district"),
        Index("ix_priority_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotspot_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    district: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    centroid_latitude: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    centroid_longitude: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    band: Mapped[str] = mapped_column(String(20), nullable=False, default=PriorityBand.LOW)

    # Each factor's 0-1 component and its weight, so a score can be re-derived
    # and audited without re-reading the intelligence database.
    factors: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    total_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ward_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sectors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    dominant_sector: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    recommendation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_cost_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: Factors with no upstream data. Explicit rather than inferred from a zero
    #: contribution, so "genuinely low" is distinguishable from "not measured".
    unmeasured_factors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    #: Source snapshot this score was computed from, for traceability.
    intelligence_source: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PriorityHistory(Base):
    """Append-only record of previous rankings.

    Written once per run *before* the new scores replace the old ones. Rows are
    keyed by run so an interrupted run leaves no partial history, and the
    score-to-score comparison a reader wants (``movement``) is computed by the
    service layer rather than stored, since it depends on which history depth is
    being compared.
    """

    __tablename__ = "priority_history"
    __table_args__ = (
        UniqueConstraint("hotspot_code", "run_id", name="uq_history_hotspot_run"),
        Index("ix_history_run", "run_id"),
        Index("ix_history_hotspot", "hotspot_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotspot_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    band: Mapped[str] = mapped_column(String(20), nullable=False, default=PriorityBand.LOW)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PriorityRun(Base):
    """Audit record for one ranking run.

    ``status`` and ``error`` exist so a failed run is visible. Without them a
    recompute that threw would leave the previous ranking in place, and the API
    would report it as current.
    """

    __tablename__ = "priority_runs"
    __table_args__ = (Index("ix_run_started", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.RUNNING)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")

    hotspots_scored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intelligence_source: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)