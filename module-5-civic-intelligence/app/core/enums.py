"""Shared vocabularies for Module 5.

Sector values mirror Module 2's ``Category`` and Module 4's ``Sector`` so a
complaint category, a mesh sector and an intelligence signal all use one
vocabulary with no translation table between them.
"""

from __future__ import annotations

from enum import Enum


class Sector(str, Enum):
    """Civic sectors carried through from the mesh."""

    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"
    ROAD = "ROAD"
    SANITATION = "SANITATION"
    HEALTHCARE = "HEALTHCARE"
    EDUCATION = "EDUCATION"
    TRANSPORT = "TRANSPORT"
    DIGITAL_CONNECTIVITY = "DIGITAL_CONNECTIVITY"
    HOUSING = "HOUSING"
    PUBLIC_SAFETY = "PUBLIC_SAFETY"
    UNKNOWN = "UNKNOWN"


class HotspotTier(str, Enum):
    """How concentrated a cluster's demand is."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    NORMAL = "NORMAL"


class TrendDirection(str, Enum):
    """Which way a ward-sector's complaints are moving."""

    WORSENING = "WORSENING"
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class RiskType(str, Enum):
    """Classes of emerging risk, each detected by an explicit rule."""

    SPIKE = "SPIKE"
    NEW_CATEGORY = "NEW_CATEGORY"
    DETERIORATING_WITH_SPEND = "DETERIORATING_WITH_SPEND"
    STALLED_ABSORPTION = "STALLED_ABSORPTION"
    CRITICAL_CONCENTRATION = "CRITICAL_CONCENTRATION"


class RiskSeverity(str, Enum):
    """Severity attached to a detected risk."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskStatus(str, Enum):
    """Lifecycle of a risk once detected.

    ``DISMISSED`` is the reviewer's verdict that the signal is not worth acting
    on. It is kept distinct from ``RESOLVED``, which claims the underlying
    problem has been dealt with.
    """

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DISMISSED = "DISMISSED"
    RESOLVED = "RESOLVED"


class ProjectStatus(str, Enum):
    """Delivery state mirrored from Module 4."""

    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"


ACTIVE_PROJECT_STATUSES = {
    ProjectStatus.PLANNED.value,
    ProjectStatus.IN_PROGRESS.value,
    ProjectStatus.DELAYED.value,
}