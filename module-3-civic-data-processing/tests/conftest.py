"""Shared test fixtures.

Environment variables are set *before* any application module is imported so
that the settings singleton points at a throwaway SQLite database and uses the
offline mock geocoder and no-op event transport.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="module3-tests-"))
_DB_PATH = _TMP_DIR / "test_civic.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"
os.environ["EVENT_PUBLISHER"] = "noop"
os.environ["GEOCODING_PROVIDER"] = "mock"
os.environ["GEOCODING_FALLBACK_PROVIDER"] = "mock"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["POSTGIS_ENABLED"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LOG_JSON"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import models_registry  # noqa: E402,F401  (registers all models)
from app.database.base import Base  # noqa: E402
from app.database.connection import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_database():
    """Every test starts from an empty schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_payload() -> dict:
    """The reference Module 2 payload from the specification."""
    return {
        "request_id": "REQ-10023",
        "language": "ta",
        "category": "road",
        "sub_category": "pothole",
        "description": "இந்த சாலையில் நிறைய பள்ளங்கள் உள்ளன",
        "severity": 4,
        "location": {"latitude": 12.9249, "longitude": 80.1000},
        "created_at": "2026-09-13T10:30:00Z",
    }
