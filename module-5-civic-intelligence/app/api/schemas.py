"""Request/response schemas for Module 5."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --- health ------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    database_ready: bool
    mesh_available: bool
    counts: dict[str, int] = {}
    districts: list[str] = []


# --- hotspots ----------------------------------------------------------------
class HotspotOut(BaseModel):
    hotspot_code: str
    district: str
    latitude: float
    longitude: float
    ward_codes: list[str] = []
    ward_count: int
    sectors: list[str] = []
    window_days: int
    total_complaints: int
    critical_complaints: int
    population: int
    mean_intensity: float
    peak_intensity: float
    peak_severity: float
    intensity_z_score: float
    tier: str
    computed_at: datetime


class HotspotSummaryOut(BaseModel):
    window_days: int
    hotspot_count: int
    tiers: dict[str, int] = {}
    districts: list[dict] = []
    coverage: dict[str, Any] = {}


class HotspotListOut(BaseModel):
    window_days: int
    count: int
    hotspots: list[HotspotOut] = []


# --- trends ------------------------------------------------------------------
class TrendOut(BaseModel):
    ward_code: str
    district: Optional[str] = None
    sector: str
    window_days: int
    direction: str
    first_count: int
    last_count: int
    sample_count: int
    pct_change: float
    slope_per_window: float
    momentum: float
    volatility: float
    series: list[int] = []
    computed_at: datetime


class TrendSummaryOut(BaseModel):
    directions: dict[str, int] = {}
    sectors: dict[str, dict[str, int]] = {}
    mean_slope: float
    emerging_sectors: list[TrendOut] = []


class TrendListOut(BaseModel):
    count: int
    trends: list[TrendOut] = []


# --- risks -------------------------------------------------------------------
class RiskOut(BaseModel):
    risk_code: str
    risk_type: str
    rule: str
    ward_code: Optional[str] = None
    district: Optional[str] = None
    sector: Optional[str] = None
    severity: str
    confidence: float
    title: str
    description: str
    evidence: dict[str, Any] = {}
    status: str
    detected_at: datetime


class RiskSummaryOut(BaseModel):
    total: int
    open: int
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    confirmatory: int = 0


class RiskListOut(BaseModel):
    count: int
    risks: list[RiskOut] = []


class RiskStatusIn(BaseModel):
    """Review action an officer takes on a risk."""

    status: str = Field(..., pattern="^(OPEN|ACKNOWLEDGED|DISMISSED|RESOLVED)$")


# --- operations --------------------------------------------------------------
class SyncOut(BaseModel):
    synced_at: datetime
    counts: dict[str, int] = {}
    inserted: dict[str, int] = {}
    mesh_available: bool


class AnalysisOut(BaseModel):
    run_id: int
    window_days: int
    wards_analysed: int
    hotspots: int
    trends: int
    risks: int
    tiers: dict[str, int] = {}
    directions: dict[str, int] = {}
    risks_summary: dict[str, Any] = {}
    duration_ms: int


class RunOut(BaseModel):
    run_id: int
    run_at: datetime
    kind: str
    window_days: int
    wards_analysed: int
    hotspots_created: int
    trends_computed: int
    risks_detected: int
    duration_ms: int


class WardBriefOut(BaseModel):
    ward_code: str
    name: str
    district: str
    latitude: float
    longitude: float
    population: int
    complaints: int
    critical_complaints: int
    intensity: float
    sectors: list[str] = []