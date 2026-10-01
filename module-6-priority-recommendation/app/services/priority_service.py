"""The priority score itself.

Five factors, each normalised against a fixed reference, combined into a 0-100
composite and cut into a band. Two properties matter more than the arithmetic:

**Unmeasured is not zero.** A factor with no upstream data contributes nothing
and is recorded as unmeasured. This is the single most important behaviour in
the module - it is the difference between "we looked and found nothing" and "we
never looked", which are very different things to act on.

**Pure.** Scoring takes signals and returns a score. It touches no database, no
clock and no randomness, so a score can be re-derived from a stored snapshot and
must produce the same number.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.config import Settings, settings
from app.core.enums import FACTOR_LABELS, PriorityBand, ScoreFactor, TrendDirection
from app.core.utils import clamp
from app.services.intelligence_client import HotspotSignals, Signal, _weighted_growth


def band_for(score: float, config: Settings | None = None) -> PriorityBand:
    """Cut a 0-100 composite into a band."""
    cfg = config or settings
    if score >= cfg.bands.high:
        return PriorityBand.HIGH
    if score >= cfg.bands.medium:
        return PriorityBand.MEDIUM
    return PriorityBand.LOW


def score_hotspot(
    hotspot: HotspotSignals, config: Settings | None = None
) -> dict[str, Any]:
    """Score one hotspot and return the score with its full working.

    Returns a plain dict rather than a dataclass because this is the shape that
    gets persisted as JSON evidence, and keeping one representation avoids the
    conversion layer where those two usually drift apart.
    """
    cfg = config or settings
    refs = cfg.references
    weights = cfg.weights
    unmeasured: list[str] = list(hotspot.unmeasured_factors)

    # -- demand ------------------------------------------------------------
    if hotspot.population > 0 and hotspot.demand_rate_per_1000 is not None:
        demand = clamp(hotspot.demand_rate_per_1000 / refs.ref_max_complaints_per_1000)
        demand_signal = Signal(
            ScoreFactor.DEMAND,
            hotspot.demand_rate_per_1000,
            demand,
            weights.demand,
            True,
            f"{hotspot.demand_rate_per_1000:.1f} complaints per 1,000 residents",
        )
    else:
        demand_signal = Signal(
            ScoreFactor.DEMAND,
            None,
            0.0,
            weights.demand,
            False,
            "population or demand data unavailable",
        )
        if ScoreFactor.DEMAND not in unmeasured:
            unmeasured.append(ScoreFactor.DEMAND)

    # -- severity ----------------------------------------------------------
    if hotspot.avg_severity is not None:
        severity = clamp(hotspot.avg_severity / refs.ref_max_avg_severity)
        severity_signal = Signal(
            ScoreFactor.SEVERITY,
            hotspot.avg_severity,
            severity,
            weights.severity,
            True,
            f"average severity {hotspot.avg_severity:.1f} of 5",
        )
    else:
        severity_signal = Signal(
            ScoreFactor.SEVERITY, None, 0.0, weights.severity, False,
            "severity data unavailable",
        )
        if ScoreFactor.SEVERITY not in unmeasured:
            unmeasured.append(ScoreFactor.SEVERITY)

    # -- trend -------------------------------------------------------------
    if hotspot.trend_direction != TrendDirection.UNKNOWN:
        trend = _weighted_growth(hotspot.trend_direction, hotspot.pct_growth)
        growth = hotspot.pct_growth
        trend_signal = Signal(
            ScoreFactor.TREND,
            growth,
            clamp(trend),
            weights.trend,
            True,
            f"{str(hotspot.trend_direction).lower()}"
            + (f" by {abs(growth):.0f}%" if growth is not None else ""),
        )
    else:
        trend_signal = Signal(
            ScoreFactor.TREND, None, 0.0, weights.trend, False,
            "no trend data for this hotspot's sectors",
        )
        if ScoreFactor.TREND not in unmeasured:
            unmeasured.append(ScoreFactor.TREND)

    # -- coverage ----------------------------------------------------------
    if hotspot.gap_score is not None:
        coverage = clamp(hotspot.gap_score / refs.ref_max_gap_pct)
        coverage_signal = Signal(
            ScoreFactor.COVERAGE,
            hotspot.gap_score,
            coverage,
            weights.coverage,
            True,
            f"service coverage gap {hotspot.gap_score:.0f}%",
        )
    else:
        coverage_signal = Signal(
            ScoreFactor.COVERAGE, None, 0.0, weights.coverage, False,
            "no coverage gap snapshot available",
        )
        if ScoreFactor.COVERAGE not in unmeasured:
            unmeasured.append(ScoreFactor.COVERAGE)

    # -- service failure ---------------------------------------------------
    # Three delivery signals combined: money stuck, money unspent, and time
    # overrun. Each saturates independently so one extreme cannot mask the others.
    delivery_parts: list[tuple[float, float]] = []
    notes: list[str] = []
    if hotspot.stalled_share is not None:
        delivery_parts.append((clamp(hotspot.stalled_share / refs.ref_max_stalled_share), 1.0))
        notes.append(f"{hotspot.stalled_share * 100:.0f}% of budget stalled")
    if hotspot.unspent_share is not None:
        delivery_parts.append((clamp(hotspot.unspent_share / refs.ref_max_unspent_share), 1.0))
        notes.append(f"{hotspot.unspent_share * 100:.0f}% of active budget unspent")
    if hotspot.delay_days is not None:
        delivery_parts.append((clamp(hotspot.delay_days / refs.ref_max_delay_days), 1.0))
        notes.append(f"average delay {hotspot.delay_days:.0f} days")

    if delivery_parts:
        delivery = sum(value for value, _ in delivery_parts) / len(delivery_parts)
        service_signal = Signal(
            ScoreFactor.SERVICE_FAILURE,
            hotspot.delay_days,
            clamp(delivery),
            weights.service_failure,
            True,
            "; ".join(notes),
        )
    else:
        service_signal = Signal(
            ScoreFactor.SERVICE_FAILURE, None, 0.0, weights.service_failure, False,
            "no project delivery data available",
        )
        if ScoreFactor.SERVICE_FAILURE not in unmeasured:
            unmeasured.append(ScoreFactor.SERVICE_FAILURE)

    signals = {
        s.factor: s
        for s in (
            demand_signal,
            severity_signal,
            trend_signal,
            coverage_signal,
            service_signal,
        )
    }

    # An unmeasured factor contributes nothing, which means it is
    # indistinguishable from a measured zero. That is exactly the failure this
    # module exists to avoid: a district where nobody reports anything would
    # score the same as a district where nothing is wrong.
    #
    # So the composite is scaled by the share of the weight that *was* measured.
    # Partial evidence therefore always yields a lower score than the same
    # reading with full evidence, and the multiplier is reported alongside it
    # so a reader can see how much of the score rests on real measurement.
    measured_weight = sum(s.weight for s in signals.values() if s.measured)
    evidence_coverage = clamp(measured_weight)
    composite = sum(s.component * s.weight for s in signals.values())
    score = round(composite * evidence_coverage * 100.0, 1)

    return {
        "hotspot_code": hotspot.hotspot_code,
        "district": hotspot.district,
        "centroid_latitude": hotspot.centroid_latitude,
        "centroid_longitude": hotspot.centroid_longitude,
        "ward_codes": hotspot.ward_codes,
        "sectors": hotspot.sectors,
        "dominant_sector": hotspot.dominant_sector,
        "score": score,
        "band": band_for(score, cfg).value,
        # Share of the composite score resting on measured evidence. 1.0 means
        # every factor was available.
        "evidence_coverage": round(evidence_coverage, 4),
        "factors": {name: sig.as_dict() for name, sig in signals.items()},
        "factor_labels": FACTOR_LABELS,
        "unmeasured_factors": unmeasured,
        "evidence": {
            "evidence_coverage": round(evidence_coverage, 4),
            "complaints_per_1000": hotspot.demand_rate_per_1000,
            "total_complaints": hotspot.total_complaints,
            "critical_complaints": hotspot.critical_complaints,
            "population": hotspot.population,
            "avg_severity": hotspot.avg_severity,
            "pct_growth": hotspot.pct_growth,
            "trend_direction": hotspot.trend_direction,
            "gap_score": hotspot.gap_score,
            "stalled_share": hotspot.stalled_share,
            "unspent_share": hotspot.unspent_share,
            "delay_days": hotspot.delay_days,
        },
        "population": hotspot.population,
        "total_complaints": hotspot.total_complaints,
        "critical_complaints": hotspot.critical_complaints,
        "window_days": hotspot.window_days,
    }


def rank_scores(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign ranks, highest score first.

    Ties are broken by hotspot code so the order is stable between runs. An
    unstable order would make the rank column churn and make movement history
    meaningless.
    """
    ordered = sorted(scored, key=lambda row: (-row["score"], row["hotspot_code"]))
    for position, row in enumerate(ordered, start=1):
        row["rank"] = position
    return ordered


def score_snapshot(
    signals: list[HotspotSignals], config: Settings | None = None
) -> list[dict[str, Any]]:
    """Score and rank every hotspot in a snapshot."""
    return rank_scores([score_hotspot(hotspot, config) for hotspot in signals])


__all__ = ["score_hotspot", "score_snapshot", "rank_scores", "band_for"]