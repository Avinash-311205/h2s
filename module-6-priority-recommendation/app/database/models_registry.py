"""Import every model module so ``create_all`` sees the full metadata.

SQLAlchemy only registers tables whose modules have been imported. Keeping the
imports here means ``init_db`` has one obvious dependency instead of depending on
whatever happened to be imported first.
"""

from __future__ import annotations

from app.models.priority_tables import PriorityHistory, PriorityRun, PriorityScore

__all__ = ["PriorityScore", "PriorityHistory", "PriorityRun"]