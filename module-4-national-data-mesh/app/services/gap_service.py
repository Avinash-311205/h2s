"""Gap analysis: turn joined mesh data into a ranked, explainable need.

The gap score for a ``(ward, sector)`` pair is a weighted sum of three
independently auditable components, each normalised to 0-1:

``demand``
    Citizens are already telling us this is broken: complaint volume relative to
    the busiest ward in the dataset, lifted by the severity they reported.

``absence``
    There is not enough infrastructure to go round: served population against
    ward population, so an under-served ward scores high even if it is quiet.

``quality``
    The infrastructure exists but does not work: broken assets, poor condition,
    and assets old enough to be a liability.

Keeping these separate matters. A quiet under-served ward and a loud
well-served ward need different interventions, and a single blended number would
hide that difference. Every component is stored alongside the final score, so a
policymaker can see exactly which signal drove a ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.core.config import settings
from app.core.enums import GapSeverity, ProjectStatus, Sector
from app.core.utils import clamp, mean, safe_div
from app.services import capacity_service

# Severities at or above this escalate a gap's recommended action.
CRITICAL_SCORE = 75.0
HIGH_SCORE = 55.0
MODERATE_SCORE = 35.0

# An asset's condition counts more than raw volume when both are available.
_DEMAND_WEIGHT_WITHIN_COMPONENT = 0.6
_SEVERITY_WEIGHT_WITHIN_COMPONENT = 0.4


@dataclass
class GapResult:
    """One computed gap with its full, inspectable breakdown."""

    ward_code: str
    sector: str
    gap_score: float
    severity: str
    demand_score: float
    absence_score: float
    quality_score: float
    population: int
    complaints: int
    avg_severity: float
    asset_count: int
    functional_asset_count: int
    served_population: float
    coverage_ratio: float
    committed_capex_lakhs: float
    spent_capex_lakhs: float
    active_project_count: int
    recommended_action: str
    rationale: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ward_code": self.ward_code,
            "sector": self.sector,
            "gap_score": round(self.gap_score, 2),
            "severity": self.severity,
            "components": {
                "demand": round(self.demand_score, 4),
                "absence": round(self.absence_score, 4),
                "quality": round(self.quality_score, 4),
            },
            "population": self.population,
            "complaints": self.complaints,
            "avg_severity": round(self.avg_severity, 3),
            "asset_count": self.asset_count,
            "functional_asset_count": self.functional_asset_count,
            "served_population": round(self.served_population, 1),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "committed_capex_lakhs": round(self.committed_capex_lakhs, 2),
            "spent_capex_lakhs": round(self.spent_capex_lakhs, 2),
            "active_project_count": self.active_project_count,
            "recommended_action": self.recommended_action,
            "rationale": self.rationale,
            "evidence": self.evidence,
        }


def compute_gap(
    *,
    ward_code: str,
    sector: str,
    population: int,
    demand_rows: Sequence,
    assets: Sequence,
    projects: Sequence,
    reference_year: Optional[int] = None,
    demand_reference: Optional[int] = None,
    coverage_reference: Optional[float] = None,
) -> GapResult:
    """Compute the gap for one ward and sector.

    Args:
        ward_code: the ward being assessed.
        sector: civic sector.
        population: ward population (from GIS/census).
        demand_rows: aggregated :class:`CitizenDemand` rows for this ward; rows
            from other sectors are ignored.
        assets: the ward's asset rows.
        projects: the ward's investment project rows.
        reference_year: year for asset-age maths.
        demand_reference: highest complaint count seen for *this sector* across
            the dataset; supplied by the caller so the normalisation is global
            rather than per-ward (otherwise a single quiet dataset would look
            severe).
        coverage_reference: optional override for the coverage target.
    """
    provision = capacity_service.summarise_provision(
        sector=sector, population=population, assets=assets, reference_year=reference_year
    )
    # Callers pass the ward's whole demand table, so narrow it to this sector
    # first. Without this filter every sector in a ward would inherit the ward's
    # total complaint count and score as if it were that sector's problem.
    sector_rows = [row for row in demand_rows if row.sector == sector]
    complaints = sum(int(row.complaint_count or 0) for row in sector_rows)
    severity_samples = [
        float(row.avg_severity) for row in sector_rows if int(row.complaint_count or 0) > 0
    ]
    avg_severity = mean(severity_samples)
    critical_count = sum(int(row.critical_count or 0) for row in sector_rows)

    demand = _demand_score(complaints, avg_severity, demand_reference)
    absence_measurable = capacity_service.has_capacity_metric(sector)
    absence = _absence_score(
        provision.coverage_ratio, coverage_reference, measurable=absence_measurable
    )
    # A 0 quality index is a total failure, so invert onto a 0-1 "badness".
    quality = 1.0 - provision.quality_index

    weights = settings.gap_weights
    gap_score = clamp(
        100.0
        * (
            weights["demand"] * demand
            + weights["absence"] * absence
            + weights["quality"] * quality
        ),
        0.0,
        100.0,
    )

    committed, spent, active = _project_totals(projects, sector)
    action, rationale = _recommend(
        gap_score=gap_score,
        sector=sector,
        complaints=complaints,
        critical_count=critical_count,
        asset_count=provision.asset_count,
        functional_count=provision.functional_asset_count,
        coverage=provision.coverage_ratio,
        active_projects=active,
    )

    return GapResult(
        ward_code=ward_code,
        sector=sector,
        gap_score=gap_score,
        severity=band_for_score(gap_score),
        demand_score=demand,
        absence_score=absence,
        quality_score=quality,
        population=population,
        complaints=complaints,
        avg_severity=avg_severity,
        asset_count=provision.asset_count,
        functional_asset_count=provision.functional_asset_count,
        served_population=provision.served_population,
        coverage_ratio=provision.coverage_ratio,
        committed_capex_lakhs=committed,
        spent_capex_lakhs=spent,
        active_project_count=active,
        recommended_action=action,
        rationale=rationale,
        evidence={
            "degraded_asset_count": provision.degraded_asset_count,
            "broken_asset_codes": provision.broken_asset_codes,
            "mean_asset_age_years": round(provision.mean_age_years, 2),
            "quality_index": round(provision.quality_index, 4),
            "critical_complaints": critical_count,
            "sector_demand_reference": demand_reference,
            "absence_measurable": absence_measurable,
            "asset_types": provision.asset_types,
        },
    )


def _demand_score(complaints: int, avg_severity: float, demand_reference: Optional[int]) -> float:
    """Volume relative to the dataset maximum, lifted by reported severity.

    Severity is normalised from the 1-5 civic scale to 0-1 by ``(v - 1) / 4``, so
    a severity-1 complaint contributes nothing and a severity-5 one contributes
    fully.
    """
    if complaints <= 0:
        return 0.0
    reference = demand_reference or complaints
    volume_share = clamp(safe_div(complaints, reference), 0.0, 1.0)
    severity_share = clamp(safe_div(avg_severity - 1.0, 4.0), 0.0, 1.0)
    return clamp(
        _DEMAND_WEIGHT_WITHIN_COMPONENT * volume_share
        + _SEVERITY_WEIGHT_WITHIN_COMPONENT * severity_share,
        0.0,
        1.0,
    )


def _absence_score(
    coverage_ratio: float,
    coverage_reference: Optional[float] = None,
    *,
    measurable: bool = True,
) -> float:
    """How far short of the target coverage this ward falls.

    ``coverage_ratio >= 1`` means the assets can serve everyone, which scores 0.
    When the sector has no population-per-asset metric (a road, say) coverage is
    not measurable, so this returns 0 rather than the 1.0 a fabricated 0%
    coverage would produce.
    """
    if not measurable:
        return 0.0
    target = coverage_reference or settings.coverage_target_ratio
    if target <= 0:
        return 0.0
    if coverage_ratio >= target:
        return 0.0
    return clamp(1.0 - safe_div(coverage_ratio, target), 0.0, 1.0)


def _project_totals(projects: Sequence, sector: str) -> tuple[float, float, int]:
    """Committed capital, realised capital and live project count for a sector.

    Cancelled work is excluded: money spent on a cancelled project bought no
    capacity, so counting it would hide a delivery failure.
    """
    committed = 0.0
    spent = 0.0
    active = 0
    for project in projects:
        if project.sector != sector:
            continue
        if project.status == ProjectStatus.CANCELLED.value:
            continue
        committed += float(project.budget_lakhs or 0.0)
        spent += float(project.spent_lakhs or 0.0)
        if project.status in {
            ProjectStatus.PLANNED.value,
            ProjectStatus.IN_PROGRESS.value,
            ProjectStatus.DELAYED.value,
        }:
            active += 1
    return committed, spent, active


def band_for_score(score: float) -> str:
    """Map a 0-100 gap score onto a :class:`GapSeverity` band."""
    if score >= CRITICAL_SCORE:
        return GapSeverity.CRITICAL.value
    if score >= HIGH_SCORE:
        return GapSeverity.HIGH.value
    if score >= MODERATE_SCORE:
        return GapSeverity.MODERATE.value
    return GapSeverity.LOW.value


def _recommend(
    *,
    gap_score: float,
    sector: str,
    complaints: int,
    critical_count: int,
    asset_count: int,
    functional_count: int,
    coverage: float,
    active_projects: int,
) -> tuple[str, str]:
    """Choose an action and write the reason in plain language.

    The action is chosen from *what is actually wrong*, not from the score
    alone: a broken network needs repair, a missing one needs building, and a
    ward already covered but complaining loudly needs a different response.
    """
    sector_label = sector.replace("_", " ").title()

    if asset_count == 0:
        return (
            "BUILD_INFRASTRUCTURE",
            f"{sector_label}: no assets exist in this ward yet "
            f"(coverage {coverage:.0%} of population).",
        )

    if critical_count > 0 and functional_count < asset_count:
        plural = "" if critical_count == 1 else "s"
        return (
            "URGENT_REPAIR",
            f"{sector_label}: {critical_count} critical complaint{plural} and "
            f"{asset_count - functional_count} of {asset_count} assets not functional.",
        )

    if coverage < settings.coverage_target_ratio:
        return (
            "EXPAND_CAPACITY",
            f"{sector_label}: assets serve only {coverage:.0%} of the ward population.",
        )

    if gap_score >= HIGH_SCORE:
        return (
            "ACCELERATE_PROJECTS",
            f"{sector_label}: coverage looks adequate but {complaints} complaint"
            f"{'' if complaints == 1 else 's'} indicate quality problems.",
        )

    if gap_score >= MODERATE_SCORE:
        return (
            "IMPROVE_QUALITY",
            f"{sector_label}: maintenance and condition improvement needed "
            f"({complaints} complaints).",
        )

    if active_projects > 0:
        return (
            "MONITOR",
            f"{sector_label}: {active_projects} project(s) already under way.",
        )

    return ("MONITOR", f"{sector_label}: no significant gap detected.")


def sector_defaults() -> list[str]:
    """Sectors scored when the caller does not restrict the run."""
    return capacity_service.all_sectors()


def sector_demand_references(
    demand_by_ward: dict[str, list], sectors: Sequence[str]
) -> dict[str, int]:
    """Busiest ward's complaint count for each sector.

    Normalising within a sector is what makes the scores comparable *across*
    sectors. A single global maximum would let one very noisy water ward shrink
    every digital-connectivity complaint in the country to near zero, so a ward
    that is genuinely worst off for wifi could never rank.
    """
    references: dict[str, int] = {}
    for sector in sectors:
        references[sector] = max(
            (
                sum(
                    int(row.complaint_count or 0)
                    for row in rows
                    if row.sector == sector
                )
                for rows in demand_by_ward.values()
            ),
            default=0,
        )
    return references


def run_gap_analysis(
    *,
    wards: Sequence,
    demand_by_ward: dict[str, list],
    assets_by_ward: dict[str, list],
    projects_by_ward: dict[str, list],
    sectors: Optional[Sequence[str]] = None,
    reference_year: Optional[int] = None,
    demand_reference: Optional[int] = None,
    coverage_reference: Optional[float] = None,
) -> list[GapResult]:
    """Run gap analysis across every ward and sector.

    ``demand_reference`` overrides the per-sector maximum for all sectors, which
    tests use to pin the normalisation; production runs use
    :func:`sector_demand_references`.
    """
    sectors = list(sectors) if sectors else sector_defaults()
    references = sector_demand_references(demand_by_ward, sectors)
    if demand_reference is not None:
        references = {sector: demand_reference for sector in sectors}

    results: list[GapResult] = []
    scorable = {sector.value for sector in Sector if sector is not Sector.UNKNOWN}
    for ward in wards:
        ward_demand = demand_by_ward.get(ward.ward_code, [])
        ward_assets = assets_by_ward.get(ward.ward_code, [])
        ward_projects = projects_by_ward.get(ward.ward_code, [])
        for sector in sectors:
            if sector not in scorable:
                continue
            results.append(
                compute_gap(
                    ward_code=ward.ward_code,
                    sector=sector,
                    population=ward.population,
                    demand_rows=ward_demand,
                    assets=ward_assets,
                    projects=ward_projects,
                    reference_year=reference_year,
                    demand_reference=references.get(sector),
                    coverage_reference=coverage_reference,
                )
            )

    results.sort(key=lambda result: result.gap_score, reverse=True)
    return results


def current_year() -> int:
    """Year used for asset-age maths."""
    return date.today().year