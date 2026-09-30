"""Shared fixtures.

``DATABASE_URL`` is set before anything imports ``app.core.config``, so the whole
suite runs against an in-memory SQLite database and can never touch a developer's
real ``civic_intelligence.db``.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["MESH_DATABASE_URL"] = "sqlite:///./__absent_mesh__.db"
os.environ["AUTO_CREATE_SCHEMA"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database.base import Base  # noqa: E402
from app.database.connection import SessionLocal, build_engine, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def engine():
    """One in-memory engine for the session, with the schema created once."""
    test_engine = build_engine("sqlite://")
    from app.database import models_registry  # noqa: F401  (populates metadata)

    Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


def _truncate(session: Session) -> None:
    """Empty every table between tests.

    Explicit truncation rather than a rolled-back transaction: an in-memory
    SQLite engine with a ``StaticPool`` has exactly one connection underneath,
    so a test that commits can make another test's rollback ineffective. Deleting
    the rows makes the isolation unconditional instead of incidental.
    """
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()


@pytest.fixture
def db(engine):
    """Per-test session over the in-memory database, emptied first."""
    session = Session(bind=engine)
    _truncate(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(engine):
    """TestClient wired to the same in-memory database as ``db``, emptied first."""

    def _get_db():
        session = Session(bind=engine)
        try:
            yield session
            session.commit()
        finally:
            session.close()

    session = Session(bind=engine)
    _truncate(session)
    session.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- builders ---------------------------------------------------------------
@pytest.fixture
def make_ward():
    """Factory for ward rows, so a test only states what it cares about."""

    def _make(code: str, **overrides):
        from app.models.intelligence_tables import WardLocation

        defaults = {
            "ward_code": code,
            "name": f"Ward {code}",
            "district": "Chennai",
            "state": "Tamil Nadu",
            "latitude": 13.08,
            "longitude": 80.27,
            "population": 10_000,
        }
        defaults.update(overrides)
        return WardLocation(**defaults)

    return _make


@pytest.fixture
def make_demand(make_ward):
    """Factory for demand windows; ``end`` is relative to now in days."""

    def _make(ward_code: str, sector: str, complaint_count: int, *, end_days_ago: int = 0,
              window_days: int = 30, district: str = "Chennai", **overrides):
        from app.models.intelligence_tables import DemandWindow

        end = datetime.utcnow() - timedelta(days=end_days_ago)
        defaults = {
            "ward_code": ward_code,
            "district": district,
            "sector": sector,
            "window_start": end - timedelta(days=window_days),
            "window_end": end,
            "window_days": window_days,
            "complaint_count": complaint_count,
            "critical_count": 0,
            "avg_severity": 3.0,
            "population": 0,
            "source": "test",
        }
        defaults.update(overrides)
        return DemandWindow(**defaults)

    return _make