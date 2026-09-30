"""Capacity and quality: how much of a ward a sector's assets actually serve.

Raw asset counts are meaningless on their own -- one borewell and one street
light are not comparable. Every asset type is therefore converted to a
population it can serve, and only *functional* assets are counted, because an
installed-but-broken transformer serves nobody. That is precisely the
distinction this module exists to make.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.core.config import settings
from app.core.enums import AssetStatus, ConditionGrade, Sector
from app.core.utils import (
    ASSET_POPULATION_CAPACITY,
    CONDITION_SEVERITY,
    SECTOR_ASSET_TYPES,
    clamp,
    mean,
    safe_div,
)

# A functional factor per asset status: what share of nominal capacity a status
# really delivers. A broken asset serves nobody; a partial one serves half.
STATUS_FUNCTIONAL_FACTOR: dict[str, float] = {
    AssetStatus.FUNCTIONAL.value: 1.0,
    AssetStatus.PARTIAL.value: 0.5,
    AssetStatus.UNDER_REPAIR.value: 0.25,
    AssetStatus.BROKEN.value: 0.0,
    AssetStatus.NOT_STARTED.value: 0.0,
}


@dataclass
class SectorProvision:
    """Provision summary for one ward and sector."""

    sector: str
    population: int
    asset_count: int
    functional_asset_count: int
    degraded_asset_count: int
    served_population: float
    coverage_ratio: float
    quality_index: float
    mean_age_years: float
    asset_types: list[str] = field(default_factory=list)
    broken_asset_codes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sector": self.sector,
            "population": self.population,
            "asset_count": self.asset_count,
            "functional_asset_count": self.functional_asset_count,
            "degraded_asset_count": self.degraded_asset_count,
            "served_population": round(self.served_population, 1),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "quality_index": round(self.quality_index, 4),
            "mean_age_years": round(self.mean_age_years, 2),
            "asset_types": self.asset_types,
            "broken_asset_codes": self.broken_asset_codes,
        }


def asset_types_for(sector: str) -> list[str]:
    """Asset types that provide capacity for ``sector``."""
    return list(SECTOR_ASSET_TYPES.get(sector, []))


def has_capacity_metric(sector: str) -> bool:
    """Whether a sector's assets can be converted into a population served.

    Not every sector is measurable this way. A road has no "people served per
    unit" figure, so computing coverage for one would always yield 0 and make
    every ward look like it had no roads. For those sectors the absence term of
    the gap score is treated as unmeasured (contributing nothing) and the ward is
    judged on demand and quality alone, rather than on a fabricated 0%.
    """
    return any(
        ASSET_POPULATION_CAPACITY.get(asset_type, 0) > 0
        for asset_type in asset_types_for(sector)
    )


def effective_age_years(asset, reference_year: Optional[int] = None) -> int:
    """Age of an asset in years, from ``installed_year``."""
    year = reference_year or date.today().year
    return max(0, year - int(asset.installed_year or year))


def is_degraded(asset, reference_year: Optional[int] = None) -> bool:
    """An asset is degraded if its condition is poor or it is simply too old.

    Age is included deliberately: a transformer can be reported ``GOOD`` by an
    inspector while being twenty years old, and letting that pass would hide
    the exact backlog this module is meant to surface.
    """
    if asset.condition in {ConditionGrade.POOR.value, ConditionGrade.CRITICAL.value}:
        return True
    if asset.status != AssetStatus.FUNCTIONAL.value:
        return True
    return effective_age_years(asset, reference_year) >= settings.degradation_age_years


def asset_served_population(asset) -> float:
    """People actually served by one asset right now."""
    per_unit = ASSET_POPULATION_CAPACITY.get(asset.asset_type, 0)
    units = max(1, int(asset.capacity_units or 1))
    factor = STATUS_FUNCTIONAL_FACTOR.get(asset.status, 0.0)
    return per_unit * units * factor


def summarise_provision(
    sector: str,
    population: int,
    assets: Sequence,
    reference_year: Optional[int] = None,
) -> SectorProvision:
    """Summarise how well a ward's assets serve ``population`` for ``sector``.

    Args:
        sector: civic sector being assessed.
        population: ward population to serve.
        assets: the ward's asset rows (any sector; non-matching types are
            filtered out here so callers can pass a whole ward inventory).
        reference_year: year used for age maths, defaults to the current year.
    """
    relevant_types = set(asset_types_for(sector))
    relevant = [a for a in assets if a.asset_type in relevant_types]

    asset_count = len(relevant)
    functional_count = sum(
        1 for a in relevant if a.status == AssetStatus.FUNCTIONAL.value
    )
    degraded_count = sum(1 for a in relevant if is_degraded(a, reference_year))
    broken_codes = [
        a.asset_code
        for a in relevant
        if a.status in {AssetStatus.BROKEN.value, AssetStatus.NOT_STARTED.value}
    ]
    served = sum(asset_served_population(a) for a in relevant)

    coverage = safe_div(served, max(population, 1))
    quality = _quality_index(relevant, reference_year)
    ages = [effective_age_years(a, reference_year) for a in relevant]

    return SectorProvision(
        sector=sector,
        population=population,
        asset_count=asset_count,
        functional_asset_count=functional_count,
        degraded_asset_count=degraded_count,
        served_population=served,
        coverage_ratio=coverage,
        quality_index=quality,
        mean_age_years=mean([float(age) for age in ages]),
        asset_types=sorted(relevant_types),
        broken_asset_codes=sorted(broken_codes),
    )


def _quality_index(assets: Sequence, reference_year: Optional[int] = None) -> float:
    """0-1 quality of a sector's assets, combining condition and age.

    Returns ``1.0`` for an empty asset set: with nothing to go wrong there is no
    quality penalty, and the *absence* term of the gap score is what captures a
    missing network.
    """
    if not assets:
        return 1.0

    condition_scores = [
        CONDITION_SEVERITY.get(a.condition, 1) / 3.0 for a in assets
    ]
    condition_component = 1.0 - mean(condition_scores)

    ages = [effective_age_years(a, reference_year) for a in assets]
    max_age = max(ages) if ages else 0
    age_component = 1.0 - clamp(safe_div(max_age, settings.degradation_age_years * 2), 0.0, 1.0)

    broken_share = safe_div(
        sum(1 for a in assets if a.status == AssetStatus.BROKEN.value), len(assets)
    )

    return clamp(0.5 * condition_component + 0.3 * age_component + 0.2 * (1 - broken_share), 0.0, 1.0)


def sector_coverage_targets() -> dict[str, float]:
    """People each asset type is expected to serve (exposed for the API)."""
    return dict(ASSET_POPULATION_CAPACITY)


def all_sectors() -> list[str]:
    """Sectors the mesh can score.

    Excludes ``UNKNOWN`` (no physical meaning) and any sector with no asset
    mapping at all -- housing and public safety have no asset register here, so
    scoring them would emit a phantom gap for every ward in the country.
    """
    return [
        sector.value
        for sector in Sector
        if sector is not Sector.UNKNOWN and SECTOR_ASSET_TYPES.get(sector.value)
    ]