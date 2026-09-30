"""Shared vocabularies for Module 4.

These enums are the contract between the data mesh, the gap analysis and
Module 5. Sector names deliberately mirror Module 2's ``Category`` values so a
complaint category maps to a sector without a translation table.
"""

from __future__ import annotations

from enum import Enum


class Sector(str, Enum):
    """Civic sectors carried through the mesh."""

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


class AssetType(str, Enum):
    """Physical infrastructure tracked per ward."""

    WATER_SUPPLY = "WATER_SUPPLY"
    BOREWELL = "BOREWELL"
    ELECTRICITY_POLE = "ELECTRICITY_POLE"
    TRANSFORMER = "TRANSFORMER"
    STREET_LIGHT = "STREET_LIGHT"
    ROAD = "ROAD"
    DRAIN = "DRAIN"
    SEWAGE_LINE = "SEWAGE_LINE"
    WASTE_BIN = "WASTE_BIN"
    HEALTH_CLINIC = "HEALTH_CLINIC"
    SCHOOL = "SCHOOL"
    BUS_STOP = "BUS_STOP"
    PUBLIC_TOILET = "PUBLIC_TOILET"
    WIFI_HOTSPOT = "WIFI_HOTSPOT"


class AssetStatus(str, Enum):
    """Operational state of an asset."""

    FUNCTIONAL = "FUNCTIONAL"
    PARTIAL = "PARTIAL"
    BROKEN = "BROKEN"
    UNDER_REPAIR = "UNDER_REPAIR"
    NOT_STARTED = "NOT_STARTED"


class ConditionGrade(str, Enum):
    """Physical condition used by the quality term of the gap score."""

    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    CRITICAL = "CRITICAL"


class ProjectStatus(str, Enum):
    """Delivery state of an investment project."""

    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"


class DataDomain(str, Enum):
    """Data-mesh domains; each is owned and quality-scored independently."""

    CITIZEN = "CITIZEN"
    GIS = "GIS"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    INVESTMENT = "INVESTMENT"
    DEMOGRAPHICS = "DEMOGRAPHICS"


class QualityStatus(str, Enum):
    """Freshness/completeness verdict for a mesh data product."""

    HEALTHY = "HEALTHY"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"


class GapSeverity(str, Enum):
    """Band for a computed sector gap, mirroring Module 2's severity idea."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Level(str, Enum):
    """Generic pass/warn/fail level for quality scoring."""

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"