"""Import every model so ``Base.metadata`` is complete before create_all.

``init_db()`` imports this module for its side effect. Keeping the list
explicit means adding a model file is a deliberate, reviewable change rather
than a silent omission.
"""

from __future__ import annotations

from app.models.understanding_event import UnderstandingEvent
from app.models.understanding_record import UnderstandingRecord

__all__ = ["UnderstandingEvent", "UnderstandingRecord"]
