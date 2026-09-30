"""Mesh catalogue: lineage, freshness and quality scoring per domain.

A data mesh that cannot answer "where did this number come from, and is it
trustworthy?" is just a join. Each domain is scored on three standard
dimensions and registered as a *data product*:

``completeness``
    Fraction of rows with the fields this domain is required to have. Missing
    centroid coordinates or an unnamed ward are completeness failures.

``validity``
    Fraction of rows whose values are actually legal (a ward with no
    population, an asset installed in the future, a project spending more than
    it was sanctioned).

``timeliness``
    How recently the domain was refreshed, relative to its own declared
    interval. A product that is two intervals stale scores 0.

The weighted score is deliberately conservative: the weakest dimension is
multiplied, so one badly broken dimension cannot be averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from app.core.enums import DataDomain, QualityStatus
from app.core.utils import clamp, safe_div, utcnow

# Completeness/validity/timeliness weights. Timeliness counts least because a
# stale dataset is usually still more useful than an invalid one.
_QUALITY_WEIGHTS = {"completeness": 0.40, "validity": 0.40, "timeliness": 0.20}

HEALTHY_THRESHOLD = 0.85
STALE_THRESHOLD = 0.60
# Products older than this multiple of their refresh interval are STALE.
_STALE_INTERVAL_MULTIPLIER = 2.0

# How often each domain is expected to be refreshed. Declared once so the
# scoring maths and the catalogue can never disagree about a domain's cadence.
REFRESH_INTERVAL_DAYS: dict[str, int] = {
    DataDomain.CITIZEN.value: 7,
    DataDomain.GIS.value: 90,
    DataDomain.INFRASTRUCTURE.value: 30,
    DataDomain.INVESTMENT.value: 60,
    DataDomain.DEMOGRAPHICS.value: 365,
}


def refresh_interval_for(domain: str) -> int:
    """Declared refresh cadence for a domain, defaulting to 30 days."""
    return REFRESH_INTERVAL_DAYS.get(domain, 30)


# Human-readable purpose per domain, stored as the catalogue description.
PRODUCT_DESCRIPTIONS: dict[str, str] = {
    DataDomain.CITIZEN.value: (
        "Aggregated complaint counts and mean severity per ward and sector, "
        "handed off from Module 2/3 understanding pipeline."
    ),
    DataDomain.GIS.value: (
        "Ward boundaries, centroids and population snapshot; the mesh join key."
    ),
    DataDomain.INFRASTRUCTURE.value: (
        "Physical asset register per ward with operational status and condition."
    ),
    DataDomain.INVESTMENT.value: (
        "Sanctioned and realised project spend per ward and sector."
    ),
    DataDomain.DEMOGRAPHICS.value: (
        "Census indicators per ward used to normalise demand and coverage."
    ),
}


@dataclass
class DomainQuality:
    """Quality verdict for one mesh domain."""

    domain: str
    record_count: int
    completeness: float
    validity: float
    timeliness: float
    quality_score: float
    status: str
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "domain": self.domain,
            "record_count": self.record_count,
            "completeness": round(self.completeness, 4),
            "validity": round(self.validity, 4),
            "timeliness": round(self.timeliness, 4),
            "quality_score": round(self.quality_score, 4),
            "status": self.status,
            "issues": self.issues,
        }


def score_domain(
    domain: str,
    record_count: int,
    completeness: float,
    validity: float,
    *,
    generated_at: Optional[datetime] = None,
    refresh_interval_days: int = 30,
) -> DomainQuality:
    """Score one domain and derive its status.

    Args:
        domain: the :class:`DataDomain` value being scored.
        record_count: rows present.
        completeness: 0-1 share of required fields populated.
        validity: 0-1 share of rows passing domain rules.
        generated_at: when the data was refreshed.
        refresh_interval_days: the domain's declared refresh cadence.
    """
    issues: list[str] = []
    if record_count == 0:
        issues.append("no records")
    if completeness < 1.0:
        issues.append(f"completeness {completeness:.0%}")
    if validity < 1.0:
        issues.append(f"validity {validity:.0%}")

    timeliness = timeliness_score(
        generated_at or utcnow(), refresh_interval_days=refresh_interval_days
    )
    if timeliness < 1.0:
        issues.append(f"timeliness {timeliness:.0%}")

    weights = _QUALITY_WEIGHTS
    score = (
        weights["completeness"] * clamp(completeness, 0.0, 1.0)
        + weights["validity"] * clamp(validity, 0.0, 1.0)
        + weights["timeliness"] * timeliness
    )
    score = round(clamp(score, 0.0, 1.0), 4)

    if record_count == 0:
        status = QualityStatus.UNAVAILABLE.value
    elif timeliness <= 0.0:
        status = QualityStatus.STALE.value
    elif score >= HEALTHY_THRESHOLD:
        status = QualityStatus.HEALTHY.value
    elif score >= STALE_THRESHOLD:
        status = QualityStatus.INCOMPLETE.value
    else:
        status = QualityStatus.STALE.value

    return DomainQuality(
        domain=domain,
        record_count=record_count,
        completeness=round(clamp(completeness, 0.0, 1.0), 4),
        validity=round(clamp(validity, 0.0, 1.0), 4),
        timeliness=round(timeliness, 4),
        quality_score=score,
        status=status,
        issues=issues,
    )


def timeliness_score(
    generated_at: datetime, *, refresh_interval_days: int = 30
) -> float:
    """1.0 when just refreshed, decaying to 0 at twice the refresh interval."""
    if refresh_interval_days <= 0:
        return 1.0
    age = _age_days(generated_at)
    if age <= 0:
        return 1.0
    full_credit_days = refresh_interval_days * _STALE_INTERVAL_MULTIPLIER
    return round(clamp(1.0 - safe_div(age, full_credit_days), 0.0, 1.0), 4)


def _age_days(moment: datetime) -> float:
    reference = utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=reference.tzinfo)
    return max(0.0, (reference - moment).total_seconds() / 86400.0)


# --- per-domain completeness / validity --------------------------------------
def assess_wards(wards: Sequence) -> DomainQuality:
    """GIS domain: needs a name, a centroid inside valid ranges, and population."""
    total = len(wards)
    if total == 0:
        return score_domain(DataDomain.GIS.value, 0, 0.0, 0.0,
                          refresh_interval_days=refresh_interval_for(DataDomain.GIS.value))

    complete = sum(
        1
        for w in wards
        if w.name and w.district and -90 <= w.centroid_latitude <= 90
        and -180 <= w.centroid_longitude <= 180
    )
    valid = sum(
        1
        for w in wards
        if w.population > 0
        and -90 <= w.centroid_latitude <= 90
        and -180 <= w.centroid_longitude <= 180
        and (w.min_latitude is None or w.min_latitude <= w.centroid_latitude <= w.max_latitude)
        and (w.min_longitude is None or w.min_longitude <= w.centroid_longitude <= w.max_longitude)
    )
    return score_domain(
        DataDomain.GIS.value, total,
        safe_div(complete, total), safe_div(valid, total),
        refresh_interval_days=refresh_interval_for(DataDomain.GIS.value),
    )


def assess_assets(assets: Sequence, *, ward_codes: Optional[set[str]] = None) -> DomainQuality:
    """Infrastructure domain: every asset must belong to a known ward."""
    total = len(assets)
    if total == 0:
        return score_domain(DataDomain.INFRASTRUCTURE.value, 0, 0.0, 0.0,
                          refresh_interval_days=refresh_interval_for(DataDomain.INFRASTRUCTURE.value))

    complete = sum(
        1 for a in assets if a.asset_code and a.ward_code and a.asset_type and a.status
    )
    valid = sum(
        1
        for a in assets
        if (not ward_codes or a.ward_code in ward_codes)
        and 1900 <= (a.installed_year or 0) <= 2100
        and (a.capacity_units or 0) >= 0
    )
    return score_domain(
        DataDomain.INFRASTRUCTURE.value, total,
        safe_div(complete, total), safe_div(valid, total),
        refresh_interval_days=refresh_interval_for(DataDomain.INFRASTRUCTURE.value),
    )


def assess_projects(projects: Sequence, *, ward_codes: Optional[set[str]] = None) -> DomainQuality:
    """Investment domain: spend may not exceed the sanctioned budget."""
    total = len(projects)
    if total == 0:
        return score_domain(DataDomain.INVESTMENT.value, 0, 0.0, 0.0,
                          refresh_interval_days=refresh_interval_for(DataDomain.INVESTMENT.value))

    complete = sum(1 for p in projects if p.project_code and p.ward_code and p.title and p.sector)
    valid = sum(
        1
        for p in projects
        if (not ward_codes or p.ward_code in ward_codes)
        and p.budget_lakhs >= 0
        and p.spent_lakhs >= 0
        and p.spent_lakhs <= p.budget_lakhs * 1.05  # 5% overrun tolerance
    )
    return score_domain(
        DataDomain.INVESTMENT.value, total,
        safe_div(complete, total), safe_div(valid, total),
        refresh_interval_days=refresh_interval_for(DataDomain.INVESTMENT.value),
    )


def assess_demand(rows: Sequence, *, ward_codes: Optional[set[str]] = None) -> DomainQuality:
    """Citizen domain: aggregates must reference a known ward and be positive."""
    total = len(rows)
    if total == 0:
        return score_domain(DataDomain.CITIZEN.value, 0, 0.0, 0.0,
                          refresh_interval_days=refresh_interval_for(DataDomain.CITIZEN.value))

    complete = sum(1 for r in rows if r.ward_code and r.sector and r.window_days > 0)
    valid = sum(
        1
        for r in rows
        if (not ward_codes or r.ward_code in ward_codes)
        and r.complaint_count > 0
        and 1 <= r.avg_severity <= 5
        and 1 <= r.max_severity <= 5
    )
    return score_domain(
        DataDomain.CITIZEN.value, total,
        safe_div(complete, total), safe_div(valid, total),
        refresh_interval_days=refresh_interval_for(DataDomain.CITIZEN.value),
    )


def refresh_all(
    *,
    wards: Sequence,
    assets: Sequence,
    projects: Sequence,
    demand: Sequence,
    generated_at: Optional[datetime] = None,
) -> list[DomainQuality]:
    """Score every domain in the mesh, sharing the ward-code reference set."""
    ward_codes = {w.ward_code for w in wards}
    return [
        assess_wards(wards),
        assess_assets(assets, ward_codes=ward_codes),
        assess_projects(projects, ward_codes=ward_codes),
        assess_demand(demand, ward_codes=ward_codes),
    ]


def lineage_for(domain: str) -> list[str]:
    """Upstream sources for each domain, for the catalogue's lineage field."""
    return {
        DataDomain.GIS.value: ["openstreetmap", "state_ward_shapefile", "delimitation_register"],
        DataDomain.CITIZEN.value: ["module-2-understanding", "module-3-processing"],
        DataDomain.INFRASTRUCTURE.value: ["asset_register", "field_inspection", "work_orders"],
        DataDomain.INVESTMENT.value: ["budget_register", "project_portfolio", "gevi"],
        DataDomain.DEMOGRAPHICS.value: ["census", "nfsa_households", "electoral_roll"],
    }.get(domain, [])


def stale_after_days(refresh_interval_days: int = 30) -> timedelta:
    """Helper for callers building a staleness cutoff."""
    return timedelta(days=refresh_interval_days * _STALE_INTERVAL_MULTIPLIER)