"""Database engine, session factory and FastAPI dependency."""

from __future__ import annotations

from typing import Any, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.logging import get_logger
from app.database.base import Base

logger = get_logger(__name__)

_IS_SQLITE = settings.database_url.startswith("sqlite")


def build_engine(database_url: str | None = None) -> Engine:
    """Create an engine configured for either SQLite (dev) or PostgreSQL (prod)."""
    url = database_url or settings.database_url
    kwargs: dict[str, Any] = {"future": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.rstrip("/") == "sqlite:":
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(
            pool_pre_ping=settings.db_pool_pre_ping,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            except Exception:  # pragma: no cover - pragma unsupported
                pass
            finally:
                cursor.close()

    return engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def init_db(target_engine: Engine | None = None) -> None:
    """Create every table declared on ``Base.metadata`` and bootstrap PostGIS."""
    from app.database import models_registry  # noqa: F401  (ensures models are imported)

    active_engine = target_engine or engine
    Base.metadata.create_all(bind=active_engine)

    if settings.postgis_enabled:
        from app.database.postgis import bootstrap_postgis

        if bootstrap_postgis(active_engine):
            logger.info("postgis_ready")


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
