"""FastAPI application for Module 3 - Civic Data Processing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import civic_router, events_router, health_router, issues_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.database.connection import SessionLocal, init_db
from app.events.publisher import publisher_for_session

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info(
        "service_starting",
        extra={
            "service": settings.service_name,
            "version": settings.version,
            "environment": settings.environment,
            "geocoding_provider": settings.geocoding_provider,
            "event_publisher": settings.event_publisher,
        },
    )

    db = SessionLocal()
    try:
        if settings.auto_create_schema:
            init_db()

        # Best-effort: deliver any event that a previous Redis outage stranded.
        try:
            summary = publisher_for_session(db).retry_pending()
            if summary["attempted"]:
                logger.info("startup_event_retry", extra=summary)
        except Exception as exc:  # never block startup on the outbox
            logger.warning("startup_event_retry_failed", extra={"error": str(exc)})
    finally:
        db.close()

    yield
    logger.info("service_stopping", extra={"service": settings.service_name})


app = FastAPI(
    title="Niti-Setu Civic Data Processing Service",
    description=(
        "Module 3 - cleans, normalizes, geolocates, de-duplicates, validates and "
        "quality-scores citizen civic requests before publishing them to the national data mesh."
    ),
    version=settings.version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Reject oversized payloads early (security: request-size limit)."""
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > settings.max_request_bytes:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={
                "detail": f"Request body exceeds the {settings.max_request_bytes} byte limit.",
                "max_request_bytes": settings.max_request_bytes,
            },
        )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    detail = errors[0].get("msg", "Invalid request payload.") if errors else "Invalid request payload."
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": detail, "errors": [{"loc": err.get("loc"), "msg": err.get("msg")} for err in errors]},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    """Never leak internals: log the detail, return a stable error envelope."""
    logger.exception("unhandled_error", extra={"error": str(exc)})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal processing error. The citizen request was preserved for reprocessing."},
    )


app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(civic_router, prefix=settings.api_prefix)
app.include_router(issues_router, prefix=settings.api_prefix)
app.include_router(events_router, prefix=settings.api_prefix)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "module": "3 - Civic Data Processing",
        "version": settings.version,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
    }
