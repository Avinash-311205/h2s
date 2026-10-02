"""FastAPI application for Module 2 - AI Understanding."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import analytics_router, health_router, understand_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.database.connection import init_db
from app.events.consumer import start_consumer, stop_consumer
from app.events.publisher import publisher_for_session
from app.database.connection import SessionLocal

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
            "asr_provider": settings.asr_provider,
            "translation_provider": settings.translation_provider,
        },
    )
    if settings.auto_create_schema:
        init_db()

    if settings.pipeline_consumer_enabled:
        # Replay anything a previous Redis outage stranded before listening
        # for new work, so Module 3 receives the backlog.
        if settings.pipeline_retry_on_startup:
            db = SessionLocal()
            try:
                summary = publisher_for_session(db).retry_pending()
                if summary["attempted"]:
                    logger.info("startup_event_retry", extra=summary)
            except Exception as exc:  # never block startup on the outbox
                logger.warning("startup_event_retry_failed", extra={"error": str(exc)})
            finally:
                db.close()
        start_consumer()
    else:
        logger.info("pipeline_consumer_disabled")

    yield
    stop_consumer()
    logger.info("service_stopping", extra={"service": settings.service_name})


app = FastAPI(
    title="Niti-Setu AI Understanding Service",
    description=(
        "Module 2 - turns multilingual citizen voice, text and photo input into "
        "structured civic understanding: language, English translation, category, "
        "sub-category, severity (1-5) and extracted entities. Runs fully offline "
        "with deterministic rules; open-weight models (Whisper, NLLB, CLIP) are "
        "used automatically when installed."
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
    if content_length and content_length.isdigit() and int(content_length) > settings.max_upload_bytes:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"detail": f"Request body exceeds the {settings.max_upload_bytes} byte limit."},
        )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    detail = errors[0].get("msg", "Invalid request payload.") if errors else "Invalid request payload."
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": detail,
            "errors": [{"loc": err.get("loc"), "msg": err.get("msg")} for err in errors],
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    """Never leak internals: log the detail, return a stable error envelope."""
    logger.exception("unhandled_error", extra={"error": str(exc)})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal understanding error. The raw request was preserved for reprocessing."},
    )


app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(understand_router, prefix=settings.api_prefix)
app.include_router(analytics_router, prefix=settings.api_prefix)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "module": "2 - AI Understanding",
        "version": settings.version,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
        "capabilities": f"{settings.api_prefix}/capabilities",
    }
