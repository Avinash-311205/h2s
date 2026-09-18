"""
api/main.py -- FastAPI application for Module 6: Priority & Recommendation.

Endpoints:
    GET /health            -- liveness probe + DB availability
    GET /hotspots          -- all detected hotspots with priority-score breakdown
    GET /recommendations   -- top N hotspots ranked by priority, with copy

The clustering pipeline is:
    load requests (sqlite3) -> DBSCAN per category (haversine, metres)
    -> priority score (scoring.py) -> ranked hotspots
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from clustering import cluster_requests
from database import RequestsDatabase
from scoring import generate_recommendation

from .schemas import (
    Hotspot,
    HotspotsResponse,
    Recommendation,
    RecommendationsResponse,
)

APP_NAME: str = "Niti-Setu Priority & Recommendation API"
APP_VERSION: str = "0.1.0"

# Default: the DB from module-1 lives one level up from this module's folder.
DEFAULT_DB_PATH: str = os.getenv(
    "CITIZEN_DB_PATH", str(Path(__file__).resolve().parent.parent.parent / "citizen_requests.db")
)

DEFAULT_RECOMMENDATION_LIMIT: int = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook: verify the DB is reachable, then serve."""
    try:
        RequestsDatabase(DEFAULT_DB_PATH)._connect()
        app.state.db_ready = True
    except Exception:
        # Fail soft at startup -- /health and /hotspots surface the error.
        app.state.db_ready = False
    yield


app = FastAPI(
    title=APP_NAME,
    description=(
        "DBSCAN hotspot detection + transparent weighted priority scoring "
        "for citizen infrastructure requests."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

# The dashboard (module-7) runs on a different origin (Vite dev server).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_hotspots() -> list:
    """Shared helper: read requests -> cluster -> score, or raise 500."""
    if not getattr(app.state, "db_ready", False):
        raise HTTPException(
            status_code=503,
            detail=f"citizen_requests.db not available at {DEFAULT_DB_PATH}",
        )
    try:
        db = RequestsDatabase(DEFAULT_DB_PATH)
        requests = db.fetch_all_requests()
        return cluster_requests(requests)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {e}") from e


def _to_hotspot_model(hs) -> Hotspot:
    """Convert a clustering.Hotspot dataclass into the API response model."""
    return Hotspot(
        id=hs.id,
        category=hs.category,
        centroid_lat=hs.centroid_lat,
        centroid_lng=hs.centroid_lng,
        region=hs.region,
        request_count=hs.request_count,
        avg_severity=hs.avg_severity,
        avg_days_open=hs.avg_days_open,
        priority_score=hs.priority_score,
        priority_factors=hs.priority_factors,
        sample_texts=hs.sample_texts,
    )


@app.get("/", tags=["Status"])
async def root() -> dict:
    return {"app": APP_NAME, "version": APP_VERSION, "status": "running"}


@app.get("/health", tags=["Status"])
async def health() -> dict:
    return {"status": "healthy", "database_ready": getattr(app.state, "db_ready", False)}


@app.get("/hotspots", response_model=HotspotsResponse, tags=["Hotspots"])
async def get_hotspots(
    category: str | None = Query(default=None, description="Filter by category"),
) -> HotspotsResponse:
    """List all detected hotspots with their transparent priority breakdown."""
    hotspots = _load_hotspots()

    if category:
        hotspots = [h for h in hotspots if h.category.lower() == category.lower()]

    models = [_to_hotspot_model(h) for h in hotspots]
    return HotspotsResponse(total_hotspots=len(models), hotspots=models)


@app.get(
    "/recommendations",
    response_model=RecommendationsResponse,
    tags=["Recommendations"],
)
async def get_recommendations(
    limit: int = Query(
        default=DEFAULT_RECOMMENDATION_LIMIT,
        ge=1,
        le=100,
        description="Number of top-ranked recommendations to return",
    ),
) -> RecommendationsResponse:
    """Top N hotspots ranked by priority score, with plain-language copy."""
    hotspots = _load_hotspots()[:limit]

    recs = []
    for h in hotspots:
        model = _to_hotspot_model(h)
        text = generate_recommendation(
            category=h.category,
            count=h.request_count,
            avg_severity=h.avg_severity,
            region_name=h.region,
            days_open=h.avg_days_open,
            priority_score=h.priority_score,
        )
        recs.append(Recommendation(**model.model_dump(), recommendation=text))

    return RecommendationsResponse(total=len(recs), recommendations=recs)