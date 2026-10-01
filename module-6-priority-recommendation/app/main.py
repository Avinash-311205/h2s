"""FastAPI application for Module 6.

Started on port 8006. The database is initialised at startup so the service can
answer ``/health`` before any recompute has been run - health reporting "nothing
scored yet" is more useful to an operator than refusing to start.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.core.logging import configure_logging
from app.database.connection import init_db

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logger.info("module 6 ready on port %d", settings.api_port)
    yield


app = FastAPI(
    title="Niti-Setu Module 6 · Priority & Recommendation Engine",
    description=(
        "Ranks civic hotspots by need using Module 5 intelligence, and turns the "
        "ranking into a funding plan."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# The dashboard runs on the Vite dev server, which is a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)