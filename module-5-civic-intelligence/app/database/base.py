"""Declarative base shared by every ORM model in this module."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all Module 5 tables."""
