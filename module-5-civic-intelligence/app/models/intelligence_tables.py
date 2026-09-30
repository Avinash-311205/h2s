"""SQLAlchemy models for Module 5 - Civic Intelligence.

The module keeps two kinds of table. **Snapshots** are read-only copies of the
mesh (demand windows, gap scores, projects) taken at each sync; they are what
makes trends and change detection possible, since a trend needs history and the
mesh only holds the present. **Intelligence outputs** are the computed hotspots,
trends and emerging risks.

Keeping the snapshots separate from the outputs is deliberate: a re-run of the
analytics must never mutate the recorded history it is analysing.
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

from app.core.utils import utcnow
from app.database.base import Base


class DemandWindow(Base):
    """One sector's complaint volume for a ward over a fixed window.

    The time-series atom behind every trend calculation. ``window_start`` and
    ``window_end`` are explicit rather than implied by ``window_days`` alone so
    two snapshots of the same window can be told apart by their bounds.
    """

    __tablename__ = "demand_windows"
    __table_args__ = (
        UniqueConstraint(
            "ward_code", "sector", "window_start", "window_end", name="uq_demand_window"
        ),
        Index("ix_demand_ward_sector", "ward_code", "sector"),
        Index("ix_demand_window_end", "window_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    sector: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    complaint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_severity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(40), nullable=False, default="mesh")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class GapSnapshot(Base):
    """A mesh gap score as it stood at sync time.

    Two snapshots of the same ward-sector are what let Module 5 tell a gap that
    is *improving* from one that is merely quiet, and to attribute a worsening
    gap to investment that is failing to land.
    """

    __tablename__ = "gap_snapshots"
    __table_args__ = (
        UniqueConstraint("ward_code", "sector", "snapshot_at", name="uq_gap_snapshot"),
        Index("ix_gap_snapshot_ward", "ward_code"),
        Index("ix_gap_snapshot_at", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    sector: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    gap_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW")
    demand_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    absence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recommended_action: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    active_project_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(40), nullable=False, default="mesh")


class ProjectSnapshot(Base):
    """Investment project state at sync time, used for delivery-side risks."""

    __tablename__ = "project_snapshots"
    __table_args__ = (
        UniqueConstraint("project_code", "snapshot_at", name="uq_project_snapshot"),
        Index("ix_project_snapshot_ward", "ward_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    ward_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Carried like the other snapshots, so a district-level query never has to
    # join back to the ward table.
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    sector: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PLANNED")
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    budget_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spent_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sanctioned_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_completion_on: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="mesh")


class WardLocation(Base):
    """Minimal ward geometry needed for spatial clustering.

    Module 5 does not need the full mesh ward table - just centroids,
    population and district - so it keeps its own small copy rather than joining
    across databases on every request.
    """

    __tablename__ = "ward_locations"
    __table_args__ = (Index("ix_ward_locations_district", "district"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    district: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Hotspot(Base):
    """A cluster of neighbouring wards whose demand is unusually concentrated.

    ``intensity`` is complaints per 1,000 residents, so a dense cluster of small
    wards can outrank a larger but quieter neighbourhood - which is the whole
    point of normalising before ranking.
    """

    __tablename__ = "hotspots"
    __table_args__ = (
        Index("ix_hotspot_tier", "tier"),
        Index("ix_hotspot_window", "window_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotspot_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    district: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    centroid_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    ward_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sectors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    total_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_intensity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    peak_intensity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    peak_severity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    intensity_z_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Trend(Base):
    """Direction of travel for one ward-sector across successive windows.

    ``direction`` is the headline, but ``momentum`` (the most recent step) and
    ``volatility`` are kept separately because a ward can be flat on average
    while swinging violently between windows.
    """

    __tablename__ = "trends"
    __table_args__ = (
        UniqueConstraint("ward_code", "sector", "window_days", name="uq_trend_grain"),
        Index("ix_trend_direction", "direction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    sector: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    first_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pct_change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    slope_per_window: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    momentum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volatility: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    series: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class EmergingRisk(Base):
    """A rule-detected signal that something is getting worse.

    Every risk carries the evidence that produced it and the rule that fired, so
    a reviewer can disagree with the threshold rather than having to reverse
    engineer the score.
    """

    __tablename__ = "emerging_risks"
    __table_args__ = (
        Index("ix_risk_type", "risk_type"),
        Index("ix_risk_status", "status"),
        Index("ix_risk_ward", "ward_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    risk_code: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    risk_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(80), nullable=False)

    ward_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    sector: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)

    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)


class IntelligenceRun(Base):
    """Audit record for one analytics pass.

    Recording what ran and what it produced is what makes a risk traceable to the
    snapshot that caused it.
    """

    __tablename__ = "intelligence_runs"
    __table_args__ = (Index("ix_run_at", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="analysis")
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    wards_analysed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hotspots_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trends_computed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risks_detected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)