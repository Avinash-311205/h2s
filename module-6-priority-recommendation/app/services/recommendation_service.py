"""Plain-language recommendations and indicative cost envelopes.

Two rules govern this module. Recommendations must say what the *evidence*
supports and nothing more - in particular, a recommendation never claims money
will be needed when the delivery data says the problem is unspent money rather
than missing money. And costs are labelled planning envelopes, because they are
derived from a fixed formula, not estimated from a schedule of works.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.config import Settings, settings
from app.core.enums import PriorityBand, TrendDirection


def build_recommendation(
    scored: dict[str, Any], config: Settings | None = None
) -> str:
    """One sentence describing what to do about a hotspot, and why."""
    cfg = config or settings
    factors = scored.get("factors", {})
    evidence = scored.get("evidence", {})
    unmeasured = scored.get("unmeasured_factors", [])

    trend = factors.get(str(TrendDirection and "trend"), {})
    direction = evidence.get("trend_direction")

    # The dominant concern drives the sentence. Coverage gap is the strongest
    # signal of unmet need, so it wins; delivery failure is checked next because
    # money already allocated and not spent is the most actionable finding.
    if evidence.get("gap_score") is not None and evidence["gap_score"] >= 40:
        action = (
            f"Expand {scored.get('dominant_sector') or 'priority'} service coverage "
            f"across the hotspot's wards"
        )
    elif evidence.get("stalled_share") and evidence["stalled_share"] >= 0.2:
        action = (
            "Escalate stalled projects - the budget is committed but not delivering"
        )
    elif evidence.get("unspent_share") and evidence["unspent_share"] >= 0.3:
        action = "Expedite release of sanctioned but unspent budget"
    elif direction == TrendDirection.WORSENING:
        action = (
            f"Intervene now - complaints are rising"
            + (
                f" by {abs(evidence['pct_growth']):.0f}%"
                if evidence.get("pct_growth") is not None
                else ""
            )
        )
    elif evidence.get("complaints_per_1000") is not None:
        action = (
            f"Investigate - {evidence['complaints_per_1000']:.1f} complaints per "
            "1,000 residents is high for this window"
        )
    else:
        action = "Investigate - available data is not sufficient to act"

    suffix = ""
    if unmeasured:
        names = ", ".join(unmeasured)
        # Naming the gaps is the point: a policymaker deciding whether to act
        # needs to know which parts of the score are missing evidence.
        suffix = f" (note: {names} not measured for this hotspot)"
    return f"{action}.{suffix}"


def build_cost_lakhs(
    scored: dict[str, Any], config: Settings | None = None
) -> float:
    """Indicative budget envelope for a hotspot.

    Scaled by ward count because a cluster's cost follows how much of the city it
    covers. Additional envelopes are added for delivery problems because those
    need recovery effort on top of new work.
    """
    cfg = config or settings
    costs = cfg.costs
    evidence = scored.get("evidence", {})

    total = costs.base_lakhs + costs.per_ward_lakhs * len(scored.get("ward_codes") or [])
    if evidence.get("stalled_share"):
        total += costs.stalled_extra_lakhs * min(2.0, float(evidence["stalled_share"]) * 4)
    if evidence.get("unspent_share"):
        total += costs.unspent_extra_lakhs * min(2.0, float(evidence["unspent_share"]) * 2)
    if evidence.get("critical_complaints"):
        total += costs.critical_extra_lakhs
    return round(total, 1)


def build_action_plan(
    scored_rows: list[dict[str, Any]], config: Settings | None = None
) -> list[dict[str, Any]]:
    """Turn the ranking into a funding plan in rank order.

    Each row carries the cumulative envelope so far, which is what a budget
    discussion actually needs - not just the individual envelopes but where the
    running total crosses a given budget.
    """
    cfg = config or settings
    plan: list[dict[str, Any]] = []
    cumulative = 0.0
    for row in scored_rows:
        cost = float(row.get("recommended_cost_lakhs") or build_cost_lakhs(row, cfg))
        cumulative += cost
        plan.append(
            {
                "rank": row["rank"],
                "hotspot_code": row["hotspot_code"],
                "district": row["district"],
                "dominant_sector": row.get("dominant_sector"),
                "score": row["score"],
                "band": row["band"],
                "recommended_cost_lakhs": cost,
                "cumulative_cost_lakhs": round(cumulative, 1),
                "recommendation": row.get("recommendation")
                or build_recommendation(row, cfg),
                "population": row.get("population", 0),
                "total_complaints": row.get("total_complaints", 0),
            }
        )
    return plan


__all__ = ["build_recommendation", "build_cost_lakhs", "build_action_plan"]