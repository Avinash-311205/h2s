"""Import every mapped class so ``Base.metadata`` is fully populated."""

from app.database.base import Base
from app.models.citizen_request import CitizenRequest
from app.models.civic_record import ProcessedCivicRecord
from app.models.event_outbox import EventOutbox
from app.models.issue_group import IssueGroup
from app.models.location import Location
from app.models.processing_error import ProcessingError

__all__ = [
    "Base",
    "CitizenRequest",
    "EventOutbox",
    "IssueGroup",
    "Location",
    "ProcessedCivicRecord",
    "ProcessingError",
]
