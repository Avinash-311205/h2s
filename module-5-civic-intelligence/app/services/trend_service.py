"""Trend detection: which ward-sectors are getting worse, and how fast.

A trend needs at least two observations to have a direction, so this service
refuses to invent one: a single window yields ``UNKNOWN`` rather than a guess.
The same principle applies to change size - a move smaller than the configured
threshold is reported as ``STABLE``, because "complaints went from 4 to 5" is
not a trend worth a district officer's time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.core.config import settings
from app.core.enums import TrendDirection
from app.core.utils import (
    linear_slope,
    mean,
    percent_change,
    stdev,
)


@dataclass
class TrendResult:
    """Direction of travel for one ward-sector."""

    ward_code: str
    sector: str
    window_days: int
    direction: str
    first_count: int
    last_count: int
    sample_count: int
    pct_change: float
    slope_per_window: float
    momentum: float
    volatility: float
    series: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ward_code": self.ward_code,
            "sector": self.sector,
            "window_days": self.window_days,
            "direction": self.direction,
            "first_count": self.first_count,
            "last_count": self.last_count,
            "sample_count": self.sample_count,
            "pct_change": round(self.pct_change, 2),
            "slope_per_window": round(self.slope_per_window, 4),
            "momentum": round(self.momentum, 2),
            "volatility": round(self.volatility, 4),
            "series": list(self.series),
        }


def classify_direction(
    pct_change: float, slope: float, threshold_pct: Optional[float] = None
) -> str:
    """Classify a change, requiring both the level and the slope to agree.

    A rising percentage with a flat slope means one bad window, not a trend. A
    falling percentage with a rising slope means a recent rebound. Requiring
    agreement between the two keeps single-window noise out of the headline.
    """
    threshold = (
        threshold_pct if threshold_pct is not None else settings.trend_change_threshold_pct
    )
    if pct_change >= threshold and slope > 0:
        return TrendDirection.WORSENING.value
    if pct_change <= -threshold and slope < 0:
        return TrendDirection.IMPROVING.value
    return TrendDirection.STABLE.value


def momentum_of(series: Sequence[int]) -> float:
    """Size of the most recent step, as a signed count.

    Reported separately from ``pct_change`` because a ward can be improving on
    average while its most recent window got sharply worse.
    """
    if len(series) < 2:
        return 0.0
    return float(series[-1] - series[-2])


def compute_trend(
    *,
    ward_code: str,
    sector: str,
    series: Sequence[int],
    window_days: int = 90,
    threshold_pct: Optional[float] = None,
) -> TrendResult:
    """Compute a trend from an ordered series of window complaint counts."""
    counts = [int(value) for value in series]

    if len(counts) < 2:
        return TrendResult(
            ward_code=ward_code,
            sector=sector,
            window_days=window_days,
            direction=TrendDirection.UNKNOWN.value,
            first_count=counts[0] if counts else 0,
            last_count=counts[-1] if counts else 0,
            sample_count=len(counts),
            pct_change=0.0,
            slope_per_window=0.0,
            momentum=0.0,
            volatility=0.0,
            series=counts,
        )

    change = percent_change(counts[0], counts[-1])
    slope = linear_slope([float(count) for count in counts])
    return TrendResult(
        ward_code=ward_code,
        sector=sector,
        window_days=window_days,
        direction=classify_direction(change, slope, threshold_pct),
        first_count=counts[0],
        last_count=counts[-1],
        sample_count=len(counts),
        pct_change=change,
        slope_per_window=slope,
        momentum=momentum_of(counts),
        volatility=round(stdev([float(count) for count in counts]), 4),
        series=counts,
    )


def build_series(rows: Sequence, window_days: int) -> list[int]:
    """Order a set of demand windows chronologically and extract counts.

    Ordering by the window's ``window_end`` (not insertion order) means a late
    sync of an older window cannot silently reorder the series.
    """
    relevant = [row for row in rows if int(row.window_days or 0) == window_days]
    relevant.sort(key=lambda row: (row.window_end, row.window_start))
    return [int(row.complaint_count or 0) for row in relevant]


def compute_all_trends(
    demand_rows: Sequence,
    *,
    windows: Optional[Sequence[int]] = None,
    threshold_pct: Optional[float] = None,
    sectors: Optional[Sequence[str]] = None,
) -> list[TrendResult]:
    """Compute a trend for every ward-sector across every configured window.

    ``sectors`` restricts the run to specific sectors, which keeps a
    sector-focused query from having to filter the (large) result set instead.
    """
    windows = list(windows) if windows else list(settings.trend_windows_days)

    grouped: dict[tuple[str, str], list] = {}
    for row in demand_rows:
        if sectors and row.sector not in sectors:
            continue
        grouped.setdefault((row.ward_code, row.sector), []).append(row)

    results: list[TrendResult] = []
    for (ward_code, sector), rows in grouped.items():
        for window_days in windows:
            series = build_series(rows, window_days)
            if not series:
                continue
            results.append(
                compute_trend(
                    ward_code=ward_code,
                    sector=sector,
                    series=series,
                    window_days=window_days,
                    threshold_pct=threshold_pct,
                )
            )

    # Worst first: worsening before stable, then improving.
    order = {
        TrendDirection.WORSENING.value: 0,
        TrendDirection.STABLE.value: 1,
        TrendDirection.IMPROVING.value: 2,
        TrendDirection.UNKNOWN.value: 3,
    }
    results.sort(key=lambda trend: (order[trend.direction], -trend.pct_change))
    return results


def summarise_directions(trends: Sequence[TrendResult]) -> dict[str, int]:
    """Count trends per direction, always reporting all four keys."""
    counts = {direction.value: 0 for direction in TrendDirection}
    for trend in trends:
        counts[trend.direction] += 1
    return counts


def emerging_sectors(trends: Sequence[TrendResult], limit: int = 10) -> list[TrendResult]:
    """The worsening ward-sectors with the sharpest recent momentum."""
    worsening = [trend for trend in trends if trend.direction == TrendDirection.WORSENING.value]
    worsening.sort(key=lambda trend: (-trend.momentum, -trend.pct_change))
    return worsening[:limit]


def sector_trend_matrix(trends: Sequence[TrendResult]) -> dict[str, dict[str, int]]:
    """``{sector: {direction: count}}``, for a single-view summary."""
    matrix: dict[str, dict[str, int]] = {}
    for trend in trends:
        matrix.setdefault(trend.sector, {})
        matrix[trend.sector][trend.direction] = matrix[trend.sector].get(trend.direction, 0) + 1
    return matrix


def average_slope(trends: Sequence[TrendResult]) -> float:
    """Mean slope across a trend set, for a single headline number."""
    return round(mean([trend.slope_per_window for trend in trends]), 4)