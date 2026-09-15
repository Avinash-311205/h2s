"""Persistence layer.

All SQLAlchemy access lives in repositories so services stay free of query
logic - and so the database layer can be swapped or scaled independently.
"""

from app.repositories.civic_repository import CivicRepository
from app.repositories.event_repository import EventOutboxRepository
from app.repositories.issue_repository import IssueRepository

__all__ = ["CivicRepository", "EventOutboxRepository", "IssueRepository"]
