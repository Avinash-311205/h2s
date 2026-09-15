"""Optional PostGIS bootstrap.

Module 3 stores coordinates as plain columns so that it runs on SQLite for local
development. When ``POSTGIS_ENABLED=true`` and the dialect is PostgreSQL we add a
real ``geometry(Point, 4326)`` column plus a GiST index, enabling spatial
indexing and PostGIS-powered queries at national scale.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.logging import get_logger
from app.core.utils import utcnow

logger = get_logger(__name__)

_POSTGIS_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS postgis",
    "ALTER TABLE locations ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326)",
    "ALTER TABLE issue_groups ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326)",
    (
        "UPDATE locations SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) "
        "WHERE geom IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ),
    (
        "UPDATE issue_groups SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) "
        "WHERE geom IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ),
    "CREATE INDEX IF NOT EXISTS ix_locations_geom_gist ON locations USING GIST (geom)",
    "CREATE INDEX IF NOT EXISTS ix_issue_groups_geom_gist ON issue_groups USING GIST (geom)",
)


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def bootstrap_postgis(engine: Engine) -> bool:
    """Enable PostGIS and expose geometry columns. Returns ``True`` on success."""
    if not is_postgres(engine):
        logger.info("postgis_skipped", extra={"dialect": engine.dialect.name})
        return False

    try:
        with engine.begin() as connection:
            for statement in _POSTGIS_STATEMENTS:
                connection.execute(text(statement))
        logger.info("postgis_bootstrapped", extra={"at": utcnow().isoformat()})
        return True
    except Exception as exc:  # pragma: no cover - depends on the target database
        # Spatial indexing is an optimisation, never a hard requirement.
        logger.warning("postgis_bootstrap_failed", extra={"error": str(exc)})
        return False
