"""Small shared helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware current time.

    SQLite does not store timezone information, so naive timestamps come back out
    of the database. Comparing those with aware datetimes raises, which is why
    everything written here is aware and everything read is normalised by
    :func:`as_utc`.
    """
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime read from SQLite to timezone-aware UTC.

    SQLite drops the offset on the way in, so a value that was written as UTC
    comes back naive. Assuming UTC is correct here because ``utcnow`` is the
    only way this module writes timestamps.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a score component into ``[low, high]``.

    Used for every factor so no single input can push the composite outside
    0-100, and so a nonsensical upstream value degrades to "full marks" rather
    than an out-of-range score.
    """
    if value != value:  # NaN
        return low
    return max(low, min(high, value))