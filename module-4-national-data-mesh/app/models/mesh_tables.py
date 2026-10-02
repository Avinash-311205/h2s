"""SQLAlchemy models for the national data mesh.

One table per mesh domain (citizen, GIS, infrastructure, investment,
demographics) plus the catalogue that tracks their quality and lineage. Keeping
domains in separate tables -- rather than one wide denormalised table -- is what
makes this a *mesh*: each domain can be refreshed from its own source on its own
schedule without touching the others.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utcnow
from app.database.base import Base


class Ward(Base):
    """A GIS administrative unit - the join key for the entire mesh.

    Every other domain references ``ward_code``, so a single geographic grain
    lets citizen demand, assets, projects and demographics be combined without
    any fuzzy matching.
    """

    __tablename__ = "wards"
    __table_args__ = (Index("ix_wards_district_name", "district", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    district: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(120), nullable=False, default="Tamil Nadu")
    zone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # --- geometry ----------------------------------------------------------
    centroid_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    min_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    area_km2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # --- derived population snapshot (refreshed with the census domain) ----
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_boundary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="synthetic")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class InfrastructureAsset(Base):
    """A physical asset owned by a ward (the infrastructure domain)."""

    __tablename__ = "infrastructure_assets"
    __table_args__ = (
        Index("ix_assets_ward_type", "ward_code", "asset_type"),
        Index("ix_assets_ward_status", "ward_code", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    ward_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("wards.ward_code"), nullable=False, index=True
    )
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="FUNCTIONAL")
    condition: Mapped[str] = mapped_column(String(20), nullable=False, default="GOOD")

    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    installed_year: Mapped[int] = mapped_column(Integer, nullable=False, default=2015)
    last_maintained_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="synthetic")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class InvestmentProject(Base):
    """A funded project (the investment domain).

    ``budget_lakhs`` is the sanctioned cost and ``spent_lakhs`` the realised
    spend; their ratio is the delivery signal Module 5 turns into a trend.
    """

    __tablename__ = "investment_projects"
    __table_args__ = (
        Index("ix_projects_ward_sector", "ward_code", "sector"),
        Index("ix_projects_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    ward_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("wards.ward_code"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    sector: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PLANNED")

    budget_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spent_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    beneficiaries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    sanctioned_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_completion_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(40), nullable=False, default="synthetic")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CitizenDemand(Base):
    """Aggregated citizen complaints per ward and sector (the citizen domain).

    This is the aggregated hand-off from Module 2/3: raw text never enters the
    mesh, only counts and mean severity. One row per
    ``(ward_code, sector, category, window)``.
    """

    __tablename__ = "citizen_demand"
    __table_args__ = (
        UniqueConstraint("ward_code", "sector", "category", "window_days", name="uq_demand_grain"),
        Index("ix_demand_ward_sector", "ward_code", "sector"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("wards.ward_code"), nullable=False, index=True
    )
    sector: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    sub_category: Mapped[str] = mapped_column(String(60), nullable=False, default="UNCLASSIFIED")

    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    complaint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_severity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_severity: Mapped[int] = mapped_column(Integer, nullable=False, default=0.0)
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    languages: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    observed_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="module-2")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CensusIndicator(Base):
    """Demographic indicators per ward (the demographics domain)."""

    __tablename__ = "census_indicators"
    __table_args__ = (
        UniqueConstraint("ward_code", "census_year", name="uq_census_ward_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("wards.ward_code"), nullable=False, index=True
    )
    census_year: Mapped[int] = mapped_column(Integer, nullable=False, default=2011)

    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slum_households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    literacy_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_monthly_income: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sc_st_population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    female_population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(40), nullable=False, default="synthetic")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DataProduct(Base):
    """Catalogue entry for a mesh dataset: lineage, freshness and quality.

    The mesh's own bookkeeping. It exists so "where did this number come from
    and can I trust it?" is answerable without reading the query that built it.
    """

    __tablename__ = "data_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_key: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    upstream_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    validity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    timeliness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(20), nullable=False, default="HEALTHY")
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    refresh_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)


class GapRecord(Base):
    """A computed ward x sector infrastructure gap, with its full breakdown.

    The score is stored with every component that produced it so a policymaker
    can see *why* a ward is ranked, and a reviewer can recompute it.
    """

    __tablename__ = "gap_records"
    __table_args__ = (
        UniqueConstraint("ward_code", "sector", name="uq_gap_ward_sector"),
        Index("ix_gap_score", "gap_score"),
        Index("ix_gap_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ward_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("wards.ward_code"), nullable=False, index=True
    )
    sector: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    gap_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW", index=True)
    demand_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    absence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_severity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    functional_asset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    served_population: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    committed_capex_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spent_capex_lakhs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    active_project_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    recommended_action: Mapped[str] = mapped_column(String(40), nullable=False, default="MONITOR")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    pipeline_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")

class CivicRecordLineage(Base):
    """One Module 3 civic record, kept at request granularity.

    ``citizen_demand`` aggregates requests per ``(ward_code, sector, category,
    window_days)`` and necessarily discards the individual submissions that
    produced it. That aggregate is what the gap score is built from, so without
    this table a ranked gap cannot be traced back to the citizens who reported
    it -- the score would be unauditable.

    This table is that missing link: exactly one row per ``request_id``, holding
    the upstream event identity (``source_event_id``) and the ward/sector the
    request was counted under. Join it to ``citizen_demand`` on
    ``(ward_code, sector, category)`` to list the submissions behind any gap.

    ``request_id`` is the correlation_id minted by Module 1 and is UNIQUE here,
    which also makes ingest idempotent: a redelivered event updates this row
    rather than double-counting the demand aggregate.
    """

    __tablename__ = "civic_record_lineage"
    __table_args__ = (
        Index("ix_lineage_ward_sector", "ward_code", "sector"),
        Index("ix_lineage_received", "received_at"),
        Index("ix_lineage_source_event", "source_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Correlation id from Module 1 (REQ-YYYY-NNNNNN).
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Event id of the Module 3 publication this row was built from.
    source_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    issue_group_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Where this request landed. ward_code is nullable because a citizen can
    # submit coordinates that resolve to no ward; the request is still recorded
    # rather than dropped.
    ward_code: Mapped[Optional[str]] = mapped_column(
        String(20), ForeignKey("wards.ward_code"), nullable=True, index=True
    )
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    location_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    sector: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN", index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN", index=True)
    sub_category: Mapped[str] = mapped_column(String(60), nullable=False, default="UNCLASSIFIED")
    severity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Upstream quality signals, kept so a reviewer can exclude soft records.
    data_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    data_quality_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    # Provenance of the payload itself.
    source_module: Mapped[str] = mapped_column(String(40), nullable=False, default="module-3")
    event_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    schema_version: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    counted_in_demand: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    event_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True
    )
