"""Declarative base shared by Module 6's tables."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all Module 6 ORM models."""