"""FastAPI application for Module 4 - National Data Mesh.

Start with:

    python -m uvicorn app.main:app --reload --port 8004

The app creates its schema on startup so a fresh clone runs with no migration
step, and serves the OpenAPI docs at ``/docs``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import gap_router, health_router, investment_router, mesh_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.database.connection import init_db

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the schema on startup and log the surface we are exposing."""
    if settings.auto_create_schema:
        init_db()
    logger.info(
        "module_started",
        extra={"service": settings.service_name, "version": settings.version},
    )
    yield


app = FastAPI(
    title="Niti-Setu Module 4 - National Data Mesh",
    description=(
        "Joins citizen demand, GIS, infrastructure, investment and demographic "
        "domains on a shared ward key to produce ranked, explainable "
        "infrastructure gaps with full lineage."
    ),
    version=settings.version,
    lifespan=lifespan,
)

# Permissive CORS: this module is a read-mostly analytics API consumed by a
# local frontend and by Module 5, so origin locking adds friction without
# protecting anything sensitive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(mesh_router, prefix=settings.api_prefix)
app.include_router(gap_router, prefix=settings.api_prefix)
app.include_router(investment_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    """Minimal landing payload so ``GET /`` is not a dead end."""
    return {
        "service": settings.service_name,
        "version": settings.version,
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "catalog": f"{settings.api_prefix}/catalog",
            "quality": f"{settings.api_prefix}/quality",
            "wards": f"{settings.api_prefix}/wards",
            "locate": f"{settings.api_prefix}/locate",
            "assets": f"{settings.api_prefix}/assets",
            "gaps": f"{settings.api_prefix}/gaps",
            "analyse": f"{settings.api_prefix}/gaps/analyse",
            "investment_summary": f"{settings.api_prefix}/investment/summary",
        },
    }