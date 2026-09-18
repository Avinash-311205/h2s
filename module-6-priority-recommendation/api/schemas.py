"""
schemas.py -- Pydantic response models for the Priority & Recommendation API.

Every endpoint response is validated against these models so the dashboard
(module-7) can rely on a stable, documented JSON contract.
"""

from pydantic import BaseModel


class FactorBreakdown(BaseModel):
    """One factor's raw value + normalised value + weighted contribution."""

    raw: float
    normalised: float
    weight: float
    contribution: float


class PriorityFactors(BaseModel):
    """The transparent breakdown behind a hotspot's priority score."""

    volume: FactorBreakdown
    severity: FactorBreakdown
    population_density: FactorBreakdown
    days_open: FactorBreakdown


class Hotspot(BaseModel):
    """A detected geographic hotspot (DBSCAN cluster) with its score."""

    id: int
    category: str
    centroid_lat: float
    centroid_lng: float
    region: str
    request_count: int
    avg_severity: float
    avg_days_open: float
    priority_score: float
    priority_factors: PriorityFactors
    sample_texts: list = []


class Recommendation(Hotspot):
    """A hotspot plus the plain-language recommendation string."""

    recommendation: str


class HotspotsResponse(BaseModel):
    """Envelope returned by GET /hotspots."""

    total_hotspots: int
    hotspots: list = []


class RecommendationsResponse(BaseModel):
    """Envelope returned by GET /recommendations."""

    total: int
    recommendations: list = []