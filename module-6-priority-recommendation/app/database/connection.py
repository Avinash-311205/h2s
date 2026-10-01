"""Engine and session management.

Two databases are involved and they are handled differently on purpose. Module
6's own database is written to and managed through a session. Module 5's
intelligence database is opened **read-only**, because it is another module's
output: this module consumes it and must never be able to damage it, however
misconfigured the environment is.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _normalise_sqlite_url(url: str) -> str:
    """Allow ``sqlite:///relative/path.db`` as well as absolute paths.

    SQLAlchemy wants four slashes for an absolute path and three for a relative
    one, which is an easy thing to get wrong in an environment variable. A
    relative URL is resolved against the module directory so the service behaves
    the same no matter which directory it is started from.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw = url[len(prefix) :]
    if raw == ":memory:" or raw.startswith(":memory:"):
        return url
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings_database_dir()) / path
        return f"sqlite:///{path.resolve()}"
    return f"sqlite:///{path}"


def settings_database_dir() -> Path:
    """Directory the module treats as the base for relative SQLite paths."""
    from app.core.config import MODULE_DIR

    return MODULE_DIR


def build_engine(database_url: str, *, read_only: bool = False) -> Engine:
    """Create an engine, applying SQLite pragmas that this workload needs.

    ``check_same_thread=False`` is required because FastAPI serves requests from
    a thread pool and the session is created in one thread and used in another.
    The foreign-key pragma is off by default in SQLite and the mesh foreign keys
    are not enforced anyway, so it stays off rather than pretending.
    """
    url = _normalise_sqlite_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):
        path = url[len("sqlite:///") :]
        if path not in (":memory:", "") and not path.startswith(":"):
            parent = Path(path).parent
            if parent and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
    if read_only and url.startswith("sqlite"):
        _apply_read_only(engine)
    return engine


def _apply_read_only(engine: Engine) -> None:
    """Best-effort read-only enforcement on a SQLite connection.

    SQLite has no read-only connect flag, so the enforcement is a pragma that
    makes writes raise. A writable file mode still allows writes via a different
    connection, which is why the repository layer is the real guarantee - this
    is a second line of defence that turns a stray write into an error instead of
    silent corruption.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_read_only(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA query_only = ON")
        finally:
            cursor.close()


#: Engine for Module 6's own database.
engine: Engine = build_engine(settings.database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_tables(bind: Engine | None = None) -> None:
    """Create Module 6's tables if they do not exist.

    ``bind`` exists so a caller that needs the tables somewhere other than the
    configured database - the seed script writing to a throwaway one - does not
    have to rebind the module-level engine, which importers have already bound
    by value.
    """
    from app.database.base import Base
    from app.database import models_registry  # noqa: F401  (registers models)

    Base.metadata.create_all(bind=bind or engine)


def init_db() -> None:
    """Create Module 6's tables in the configured database."""
    create_tables()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that always closes."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for use outside a request."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_exists(database_url: str) -> bool:
    """Whether the SQLite file behind a URL is present.

    Checked before connecting so a missing upstream produces a clear "not seeded
    yet" message rather than an empty-but-valid database that would look like a
    successfully analysed district with zero demand.
    """
    url = _normalise_sqlite_url(database_url)
    if not url.startswith("sqlite:///"):
        return True
    raw = url[len("sqlite:///") :]
    if raw.startswith(":"):
        return True
    return Path(raw).exists() or os.path.exists(raw)