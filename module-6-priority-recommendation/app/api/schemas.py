"""Response models for Module 6's API.

Schemas are explicit rather than inferred from the ORM because the API contract
is consumed by the dashboard and should not shift when a column is added. The
``source`` and ``run_id`` fields on read responses exist so a caller can tell
which snapshot it is looking at - a ranking from an hour ago and a fresh one are
not interchangeable.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="healthy, degraded or unavailable")
    service: str
    scored: int
    intelligence_available: bool
    intelligence_source: str
    degraded_factors: list[str] = []
    degraded_factor_labels: list[str] = []
    runs: int
    latest_run: Optional[dict[str, Any]] = None
    database_url: str


class IntelligenceHealthResponse(BaseModel):
    available: bool
    source: str
    hotspots: int = 0
    detail: Optional[str] = None
    degraded_factors: list[str] = []
    table_presence: dict[str, bool] = {}
    missing_tables: list[str] = []


class FactorSignal(BaseModel):
    factor: str
    value: Optional[float] = None
    component: float
    weight: float
    measured: bool
    note: str = ""


class Movement(BaseModel):
    previous_score: Optional[float] = None
    score_change: Optional[float] = None
    rank_change: Optional[int] = None


class PriorityItem(BaseModel):
    hotspot_code: str
    district: str
    rank: int
    score: float
    band: str
    centroid_latitude: float
    centroid_longitude: float
    dominant_sector: Optional[str] = None
    sectors: list[str] = []
    ward_codes: list[str] = []
    population: int = 0
    total_complaints: int = 0
    critical_complaints: int = 0
    unmeasured_factors: list[str] = []
    factors: dict[str, Any] = {}
    factor_labels: dict[str, str] = {}
    evidence: dict[str, Any] = {}
    recommendation: Optional[str] = None
    recommended_cost_lakhs: float = 0.0
    movement: Movement = Movement()
    computed_at: Optional[str] = None
    intelligence_source: Optional[str] = None


class PrioritiesResponse(BaseModel):
    priorities: list[PriorityItem]
    count: int
    bands: dict[str, int]
    source: Optional[str] = None
    run_id: Optional[int] = None
    generated_at: Optional[str] = None


class SummaryResponse(BaseModel):
    scored: int
    districts: int
    bands: dict[str, int]
    population_covered: int
    complaints_covered: int
    critical_complaints: int
    total_cost_lakhs: float
    average_score: float
    hotspots_with_unmeasured_factors: int
    run_id: Optional[int] = None
    source: Optional[str] = None
    generated_at: Optional[str] = None


class PlanItem(BaseModel):
    rank: int
    hotspot_code: str
    district: str
    dominant_sector: Optional[str] = None
    score: float
    band: str
    recommended_cost_lakhs: float
    cumulative_cost_lakhs: float
    recommendation: Optional[str] = None
    population: int = 0
    total_complaints: int = 0


class PlanResponse(BaseModel):
    items: list[PlanItem]
    count: int
    total_cost_lakhs: float
    cumulative_cost_lakhs: float
    is_partial: bool


class HistoryEntry(BaseModel):
    run_id: int
    rank: int
    score: float
    band: str
    recorded_at: Optional[str] = None


class HistoryResponse(BaseModel):
    hotspot_code: str
    current: dict[str, Any]
    entries: list[HistoryEntry]
    count: int


class RecomputeResponse(BaseModel):
    run_id: int
    scored: int
    source: str
    degraded_factors: list[str] = []
    duration_ms: int


class RunRecord(BaseModel):
    id: int
    status: str
    trigger: str
    hotspots_scored: int
    source: Optional[str] = None
    duration_ms: int
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class RunsResponse(BaseModel):
    runs: list[RunRecord]
    count: int


class ErrorResponse(BaseModel):
    detail: str