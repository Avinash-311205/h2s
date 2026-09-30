"""FastAPI application for Module 5 - Civic Intelligence.

Start with:

    python -m uvicorn app.main:app --reload --port 8005

The app creates its schema on startup so a fresh clone runs with no migration
step, and serves the OpenAPI docs at ``/docs``.

Before the intelligence endpoints return anything meaningful, the module needs
data from the mesh:

    python ../module-4-national-data-mesh/seed_mesh.py --reset   # create the mesh
    curl -X POST localhost:8005/api/v1/operations/refresh        # sync + analyse
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    health_router,
    hotspot_router,
    ops_router,
    risk_router,
    trend_router,
)
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.database.connection import init_db
from app.services import mesh_client

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the schema on startup and report whether the mesh is reachable."""
    if settings.auto_create_schema:
        init_db()
    logger.info(
        "module_started",
        extra={
            "service": settings.service_name,
            "version": settings.version,
            "mesh_available": mesh_client.mesh_available(),
        },
    )
    if not mesh_client.mesh_available():
        # Not fatal: Module 5 still serves whatever it has already synced. It is
        # worth a warning because an empty intelligence module is usually the
        # symptom of a missing seed, not of an intentional cold start.
        logger.warning("mesh_unavailable_at_startup")
    yield


app = FastAPI(
    title="Niti-Setu Module 5 - Civic Intelligence",
    description=(
        "Turns the national data mesh into civic intelligence: geographic "
        "demand hotspots, per-sector trends, and rule-detected emerging risks, "
        "each with the evidence that produced it."
    ),
    version=settings.version,
    lifespan=lifespan,
)

# Permissive CORS: this module is a read-mostly analytics API consumed by a local
# frontend and by Module 6, so origin locking adds friction without protecting
# anything sensitive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(hotspot_router, prefix=settings.api_prefix)
app.include_router(trend_router, prefix=settings.api_prefix)
app.include_router(risk_router, prefix=settings.api_prefix)
app.include_router(ops_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    """Minimal landing payload so ``GET /`` is not a dead end."""
    return {
        "service": settings.service_name,
        "version": settings.version,
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "mesh": "/health/mesh",
            "hotspots": f"{settings.api_prefix}/hotspots",
            "hotspot_summary": f"{settings.api_prefix}/hotspots/summary",
            "trends": f"{settings.api_prefix}/trends",
            "trend_summary": f"{settings.api_prefix}/trends/summary",
            "risks": f"{settings.api_prefix}/risks",
            "risk_summary": f"{settings.api_prefix}/risks/summary",
            "sync": f"{settings.api_prefix}/operations/sync",
            "analyse": f"{settings.api_prefix}/operations/analyse",
            "refresh": f"{settings.api_prefix}/operations/refresh",
            "runs": f"{settings.api_prefix}/operations/runs",
        },
    }