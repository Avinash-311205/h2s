"""Initial Module 3 schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-13

The schema is declared once on the SQLAlchemy models; this migration materializes
it for the configured database and (optionally) bootstraps PostGIS.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app.core.config import settings
from app.database import models_registry  # noqa: F401  (populates metadata)
from app.database.base import Base
from app.database.postgis import bootstrap_postgis

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    if settings.postgis_enabled:
        bootstrap_postgis(bind.engine)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
