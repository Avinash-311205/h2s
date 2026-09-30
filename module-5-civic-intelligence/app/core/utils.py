"""Numeric and statistical helpers used by the intelligence services.

Everything here is pure and dependency-free so the analytics stay auditable and
deterministic: no random seeds, no hidden state, no library defaults that differ
between versions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def naive_utc(value: datetime) -> datetime:
    """Normalise a datetime to naive UTC.

    SQLite does not store timezone information, so a value written as
    ``2026-09-30 16:31:35+00:00`` reads back as ``2026-09-30 16:31:35``. Without
    this, a de-duplication key built from a parsed timestamp never matches the
    same key read back from the database, and every sync re-inserts the rows it
    already has. Converting at the boundary keeps every stored timestamp
    directly comparable.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Constrain ``value`` to ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns ``default`` instead of raising."""
    return numerator / denominator if denominator else default


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean tolerating an empty sample."""
    return sum(values) / len(values) if values else 0.0


def variance(values: Sequence[float]) -> float:
    """Population variance (not sample), so scores are stable at small n."""
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return sum((value - average) ** 2 for value in values) / len(values)


def stdev(values: Sequence[float]) -> float:
    """Population standard deviation."""
    return math.sqrt(variance(values))


def median(values: Sequence[float]) -> float:
    """Median, used where an outlier should not move the centre."""
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile; ``fraction`` in 0-1.

    Used for tiers so a hotspot threshold keeps its meaning as the number of
    wards changes, unlike a hard-coded rank.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = clamp(fraction, 0.0, 1.0) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def z_score(value: float, population: Sequence[float]) -> float:
    """How many standard deviations ``value`` sits above the population mean.

    Returns 0 when the population has no spread: a single value cannot be an
    outlier relative to itself, and dividing by a zero stdev would be noise.
    """
    if not population:
        return 0.0
    spread = stdev(population)
    if spread <= 0:
        return 0.0
    return round(safe_div(value - mean(population), spread), 4)


def linear_slope(values: Sequence[float]) -> float:
    """Least-squares slope of ``values`` against their index.

    Sign gives direction; magnitude gives how fast. Returns 0 for fewer than two
    points, because a single point has no slope to estimate.
    """
    if len(values) < 2:
        return 0.0
    count = len(values)
    mean_x = (count - 1) / 2
    mean_y = mean(values)
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def percent_change(first: float, last: float) -> float:
    """Percentage change, treating a move up from zero as 100%.

    ``None`` is not returned: downstream JSON needs a number, and a rise from
    zero is the most severe possible change rather than an undefined one.
    """
    if first == 0:
        return 100.0 if last > 0 else 0.0
    return round((last - first) / first * 100.0, 2)


def pct(value: float, denominator: float) -> float:
    """``value`` as a 0-1 share of ``denominator``."""
    return clamp(safe_div(value, denominator), 0.0, 1.0)


def window_start(days: int, *, reference: Optional[datetime] = None) -> datetime:
    """Start of a trailing window ending at ``reference``."""
    return (reference or utcnow()) - timedelta(days=days)


def group_by(items: Sequence, key) -> dict:
    """Group a sequence into a dict of lists, preserving input order."""
    grouped: dict = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped