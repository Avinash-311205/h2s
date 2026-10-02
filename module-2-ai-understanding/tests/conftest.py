"""Shared test fixtures.

Environment variables are set *before* any application module is imported so
that the settings singleton points at a throwaway SQLite database and the
optional model providers are forced offline.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="module2-tests-"))
_DB_PATH = _TMP_DIR / "test_understanding.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LOG_JSON"] = "false"
# The Redis consumer is exercised directly in test_pipeline_events.py;
# TestClient(app) must not subscribe to a real Redis.
os.environ["PIPELINE_CONSUMER_ENABLED"] = "false"
os.environ["ASR_PROVIDER"] = "disabled"
os.environ["TRANSLATION_PROVIDER"] = "auto"
os.environ["IMAGE_PROVIDER"] = "heuristics"

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