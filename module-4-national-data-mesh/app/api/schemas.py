"""Request/response schemas for Module 4."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# --- health / catalogue ------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    database_ready: bool
    counts: dict[str, int] = {}
    domains: list[str] = []


class DomainQualityOut(BaseModel):
    domain: str
    record_count: int
    completeness: float
    validity: float
    timeliness: float
    quality_score: float
    status: str
    issues: list[str] = []


class DataProductOut(BaseModel):
    product_key: str
    name: str
    domain: str
    description: str
    upstream_sources: list[str] = []
    record_count: int
    completeness: float
    validity: float
    timeliness: float
    quality_status: str
    quality_score: float
    generated_at: datetime
    refresh_interval_days: int


# --- geography ---------------------------------------------------------------
class LocateIn(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class WardAssignmentOut(BaseModel):
    ward_code: Optional[str] = None
    ward_name: Optional[str] = None
    district: Optional[str] = None
    latitude: float
    longitude: float
    distance_m: Optional[float] = None
    method: str
    confident: bool


class WardOut(BaseModel):
    ward_code: str
    name: str
    district: str
    state: str
    zone: Optional[str] = None
    centroid_latitude: float
    centroid_longitude: float
    population: int
    households: int
    area_km2: Optional[float] = None


class DistrictSummaryOut(BaseModel):
    district: str
    state: str
    ward_count: int
    population: int


# --- gap analysis ------------------------------------------------------------
class GapComponentsOut(BaseModel):
    demand: float
    absence: float
    quality: float


class GapOut(BaseModel):
    ward_code: str
    sector: str
    gap_score: float
    severity: str
    components: GapComponentsOut
    population: int
    complaints: int
    avg_severity: float
    asset_count: int
    functional_asset_count: int
    served_population: float
    coverage_ratio: float
    committed_capex_lakhs: float
    spent_capex_lakhs: float
    active_project_count: int
    recommended_action: str
    rationale: str
    evidence: dict[str, Any] = {}
    computed_at: Optional[datetime] = None


class RunAnalysisIn(BaseModel):
    """Body for a gap-analysis run; empty means 'score everything'."""

    sectors: Optional[list[str]] = None
    district: Optional[str] = None
    persist: bool = True

    @field_validator("sectors")
    @classmethod
    def _limit_sectors(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        allowed = {
            "WATER", "ELECTRICITY", "ROAD", "SANITATION", "HEALTHCARE",
            "EDUCATION", "TRANSPORT", "DIGITAL_CONNECTIVITY", "HOUSING", "PUBLIC_SAFETY",
        }
        unknown = [sector for sector in value if sector not in allowed]
        if unknown:
            raise ValueError(f"unknown sectors: {unknown}")
        return value


class RunAnalysisOut(BaseModel):
    analysed_at: datetime
    ward_count: int
    sector_count: int
    gap_count: int
    persisted: bool
    top_gaps: list[GapOut]
    by_severity: dict[str, int]


# --- assets / projects / demand ----------------------------------------------
class AssetOut(BaseModel):
    asset_code: str
    ward_code: str
    asset_type: str
    status: str
    condition: str
    capacity_units: int
    installed_year: int
    degraded: bool


class ProjectOut(BaseModel):
    project_code: str
    ward_code: str
    title: str
    sector: str
    status: str
    budget_lakhs: float
    spent_lakhs: float
    absorption_rate: float
    beneficiaries: int
    delay_days: int


class InvestmentSummaryOut(BaseModel):
    project_count: int
    sanctioned_lakhs: float
    spent_lakhs: float
    absorption_rate: float
    completed_count: int
    delayed_count: int
    cancelled_count: int
    active_count: int
    mean_delay_days: float
    stalled_projects: list[dict[str, Any]]
    delivery_score: float


class DemandTotalOut(BaseModel):
    sector: str
    complaints: int
    avg_severity: float


class CapacityTargetsOut(BaseModel):
    """Per-asset-type population coverage, for transparency in the UI."""

    asset_population_capacity: dict[str, int]
    sector_asset_types: dict[str, list[str]]