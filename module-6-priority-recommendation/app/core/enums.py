"""Shared vocabulary for Module 6.

The bands, factor names and run states appear in the database, the API and the
scoring code. Defining them once here keeps the three from drifting apart, which
would otherwise show up as a dashboard that colours a priority the wrong way.
"""

from __future__ import annotations

from enum import StrEnum


class PriorityBand(StrEnum):
    """Composite score band, highest first."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ScoreFactor(StrEnum):
    """The five factors that make up the composite priority score."""

    DEMAND = "demand"
    SEVERITY = "severity"
    TREND = "trend"
    COVERAGE = "coverage"
    SERVICE_FAILURE = "service_failure"


class TrendDirection(StrEnum):
    """Direction of travel, using Module 5's vocabulary verbatim.

    These string values are chosen to match ``trends.direction`` in Module 5's
    database exactly. An earlier version used ``RISING``/``FALLING``, which
    matched nothing upstream: every real hotspot silently fell through to a
    zero contribution while still reporting itself as measured, and the trend
    factor quietly did nothing. Sharing the vocabulary rather than translating
    it is what prevents that class of bug recurring.
    """

    WORSENING = "WORSENING"
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class RunStatus(StrEnum):
    """Lifecycle of a ranking run.

    A run is marked FAILED with its error recorded rather than deleted, so a
    recompute that breaks is visible afterwards instead of silently leaving the
    previous ranking in place looking current.
    """

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class HealthStatus(StrEnum):
    """Overall service health as reported by ``GET /health``."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


#: Order used when reporting factors, highest influence first.
FACTOR_ORDER: tuple[str, ...] = (
    ScoreFactor.DEMAND,
    ScoreFactor.SEVERITY,
    ScoreFactor.TREND,
    ScoreFactor.COVERAGE,
    ScoreFactor.SERVICE_FAILURE,
)

#: Human-facing labels. Kept next to the enum so the wording cannot drift from
#: the value it describes.
FACTOR_LABELS: dict[str, str] = {
    ScoreFactor.DEMAND: "Citizen demand",
    ScoreFactor.SEVERITY: "Severity of complaints",
    ScoreFactor.TREND: "Rate of increase",
    ScoreFactor.COVERAGE: "Service coverage gap",
    ScoreFactor.SERVICE_FAILURE: "Delivery failure",
}