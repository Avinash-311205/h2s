"""Test fixtures.

Two databases are involved and both are temporary per test: Module 6's own, and
a Module 5-shaped intelligence snapshot. Neither is shared between tests, so a
test that mutates a score cannot affect another test's ranking.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings  # noqa: E402
from app.database import connection as db_connection  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from synthetic_snapshot import create_tables  # noqa: E402


@pytest.fixture
def intelligence_db(tmp_path: Path) -> Path:
    """An intelligence database with the tables but no rows."""
    path = tmp_path / "intelligence.db"
    conn = create_tables(path)
    conn.close()
    return path


@pytest.fixture
def empty_priority_db(tmp_path: Path) -> Path:
    path = tmp_path / "priority.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    return path


@pytest.fixture
def session(empty_priority_db: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{empty_priority_db}")
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def client(empty_priority_db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient whose session dependency uses the temporary database."""
    engine = create_engine(f"sqlite:///{empty_priority_db}")
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[db_connection.get_session] = override
    with TestClient(fastapi_app) as c:
        yield c
    fastapi_app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def missing_db(tmp_path: Path) -> str:
    """A URL to a database file that does not exist."""
    return f"sqlite:///{tmp_path / 'nope.db'}"


def use_intelligence_db(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Point every module holding a ``settings`` reference at a new intelligence DB.

    ``Settings`` is a frozen dataclass on purpose - configuration should not
    change under a running service - so tests swap in a replacement instance
    rather than assigning a field. Each module that did ``from app.core.config
    import settings`` holds its own reference, so all of them have to be
    replaced; missing one would leave the service reading a different database
    than the test set up, which is a confusing failure to debug.
    """
    from dataclasses import replace

    import app.core.config as config_module
    import app.database.connection as connection_module
    import app.services.analytics_service as analytics_module
    import app.services.intelligence_client as client_module

    original = config_module.settings
    patched = replace(original, intelligence_database_url=url)
    for module in (
        config_module,
        connection_module,
        analytics_module,
        client_module,
    ):
        monkeypatch.setattr(module, "settings", patched, raising=False)