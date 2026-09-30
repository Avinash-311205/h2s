"""Read-only client for Module 4's database.

Module 5 depends on the mesh, not on Module 4's *process*. Reading the mesh's
SQLite file directly keeps the two modules independently runnable - a sync works
with Module 4 stopped - while still giving Module 5 the snapshots it needs for
trend history.

If the mesh database is absent the client reports it as unavailable rather than
raising, so Module 5 can still serve whatever it has already synced.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.core.utils import naive_utc, utcnow

logger = get_logger(__name__)


@dataclass
class MeshSnapshot:
    """Everything Module 5 needs from the mesh, in plain rows."""

    wards: list[dict[str, Any]] = field(default_factory=list)
    demand: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "wards": len(self.wards),
            "demand": len(self.demand),
            "gaps": len(self.gaps),
            "projects": len(self.projects),
        }

    @property
    def is_empty(self) -> bool:
        return not (self.wards or self.demand or self.gaps or self.projects)


def resolve_sqlite_path(database_url: str) -> Optional[Path]:
    """Extract a filesystem path from a SQLite URL, if that is what it is.

    Returns ``None`` for non-SQLite URLs rather than guessing: pointing at
    PostgreSQL would need a different driver entirely, and silently failing
    later is worse than saying so now.
    """
    if not database_url.startswith("sqlite"):
        return None
    if database_url in {"sqlite:", "sqlite://"}:
        return None

    path_part = database_url.split("sqlite:///", 1)[-1] if "sqlite:///" in database_url else ""
    if not path_part:
        return None
    return Path(path_part).expanduser()


def mesh_available(database_url: Optional[str] = None) -> bool:
    """Whether the mesh database file exists and looks initialised."""
    path = resolve_sqlite_path(database_url or settings.mesh_database_url)
    if path is None:
        return False
    return path.exists() and path.stat().st_size > 0


def build_mesh_engine(database_url: Optional[str] = None) -> Engine:
    """Engine for the upstream mesh, read-only where SQLite allows it."""
    url = database_url or settings.mesh_database_url
    return create_engine(
        url,
        future=True,
        connect_args={"check_same_thread": False, "timeout": settings.mesh_timeout_seconds},
    )


def _as_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


@contextmanager
def mesh_session(database_url: Optional[str] = None) -> Iterator[Session]:
    """Session on the mesh database, closing cleanly even on failure."""
    engine = build_mesh_engine(database_url)
    try:
        yield sessionmaker(bind=engine, future=True)()
    finally:
        engine.dispose()


def _existing_tables(session: Session) -> set[str]:
    return set(inspect(session.get_bind()).get_table_names())


def fetch_snapshot(
    database_url: Optional[str] = None, *, districts: Optional[list[str]] = None
) -> MeshSnapshot:
    """Read wards, demand, gaps and projects from the mesh in one pass.

    Missing tables are treated as "that domain has not been synced yet" and
    skipped, so a mesh that only has GIS loaded still produces a partial (and
    honest) snapshot.

    ``districts`` scopes *every* domain, not just wards. Demand, gaps and
    projects carry no district of their own - they are keyed on ``ward_code`` -
    so they are scoped through the ward list the district resolved to. Filtering
    wards alone would leave the other three domains national, and their rows
    would then be stored against ward locations that were never copied.
    """
    snapshot = MeshSnapshot()
    with mesh_session(database_url) as session:
        tables = _existing_tables(session)

        if "wards" in tables:
            statement = (
                "SELECT ward_code, name, district, state, centroid_latitude, "
                "centroid_longitude, population FROM wards"
            )
            params: dict[str, Any] = {}
            if districts:
                placeholders = ",".join(f":d{i}" for i in range(len(districts)))
                statement += f" WHERE district IN ({placeholders})"
                params.update({f"d{i}": value for i, value in enumerate(districts)})
            snapshot.wards = [dict(row._mapping) for row in session.execute(_text(statement), params)]

        ward_clause, ward_params = _ward_scope(session, districts)

        if "citizen_demand" in tables:
            statement = (
                "SELECT ward_code, sector, category, window_days, complaint_count, "
                "critical_count, avg_severity, observed_from, observed_to "
                "FROM citizen_demand"
            )
            statement, params = _scoped(statement, ward_clause, ward_params)
            snapshot.demand = [
                dict(row._mapping) for row in session.execute(_text(statement), params)
            ]

        if "gap_records" in tables:
            statement = (
                "SELECT ward_code, sector, gap_score, severity, demand_score, "
                "absence_score, quality_score, recommended_action, "
                "active_project_count, computed_at FROM gap_records"
            )
            statement, params = _scoped(statement, ward_clause, ward_params)
            snapshot.gaps = [
                dict(row._mapping) for row in session.execute(_text(statement), params)
            ]

        if "investment_projects" in tables:
            statement = (
                "SELECT project_code, ward_code, sector, status, title, "
                "budget_lakhs, spent_lakhs, sanctioned_on, "
                "expected_completion_on, delay_days FROM investment_projects"
            )
            statement, params = _scoped(statement, ward_clause, ward_params)
            snapshot.projects = [
                dict(row._mapping) for row in session.execute(_text(statement), params)
            ]

    logger.info("mesh_snapshot_fetched", extra=log_extra(snapshot.counts()))
    return snapshot


def _ward_scope(session: Session, districts: Optional[list[str]]) -> tuple[str, dict[str, Any]]:
    """Resolve districts to a reusable ``ward_code IN (...)`` clause.

    Returns ``("", {})`` when no district filter was asked for. When a filter
    was asked for but resolves to nothing, an always-false clause is returned so
    the other domains come back empty rather than national.
    """
    if not districts:
        return "", {}
    placeholders = ",".join(f":ds{i}" for i in range(len(districts)))
    rows = session.execute(
        _text(f"SELECT ward_code FROM wards WHERE district IN ({placeholders})"),
        {f"ds{i}": value for i, value in enumerate(districts)},
    )
    codes = [row[0] for row in rows]
    if not codes:
        return " WHERE 0 = 1", {}
    markers = ",".join(f":wc{i}" for i in range(len(codes)))
    clause = f" WHERE ward_code IN ({markers})"
    return clause, {f"wc{i}": code for i, code in enumerate(codes)}


def _scoped(statement: str, clause: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Append a ward scope to a statement, or pass both through unscoped."""
    if not clause:
        return statement, {}
    return f"{statement}{clause}", dict(params)


def _text(statement: str):
    """Wrap a raw SQL string for ``session.execute``."""
    from sqlalchemy import text

    return text(statement)


def log_extra(values: dict) -> dict:
    """Flatten counts into log-safe key/value pairs."""
    return {key: value for key, value in values.items()}


def parse_observed_bounds(row: dict[str, Any]) -> tuple[datetime, datetime]:
    """``(start, end)`` for a demand row, falling back to the window length.

    SQLite returns naive datetimes; they are labelled UTC here so every window
    in the series is comparable.
    """
    start = _parse_dt(row.get("observed_from"))
    end = _parse_dt(row.get("observed_to"))
    if end is None:
        # The mesh did not date the window, so anchor it to the current day.
        # Flooring to midnight is what keeps re-syncing idempotent: within a day
        # every sync produces the same window_end, so the natural key matches and
        # nothing is inserted. A new window opens only at the next day boundary.
        #
        # Flooring to the hour was tried first and is wrong here - it made each
        # hourly sync a distinct window, appending up to 24 identical rows per ward
        # per day and manufacturing a flat "trend" out of one unchanged figure.
        days = int(row.get("window_days") or 30)
        end = naive_utc(utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    if start is None:
        days = int(row.get("window_days") or 30)
        start = end - timedelta(days=days)
    return start, end


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse any of the timestamp shapes SQLite hands back into naive UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return naive_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return naive_utc(parsed)
    return None


def mesh_url_summary() -> dict:
    """Describe the configured upstream, for the health endpoint."""
    url = settings.mesh_database_url
    path = resolve_sqlite_path(url)
    return {
        "mesh_database_url": url,
        "resolved_path": str(path) if path else None,
        "available": mesh_available(url),
    }


def sqlite_only(url: str) -> bool:
    """Guard used by the API before attempting a sync."""
    return urlparse(url).scheme in {"sqlite", ""}