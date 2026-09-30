"""Capacity and quality: how much of a ward a sector's assets actually serve."""

from __future__ import annotations

from app.core.enums import AssetStatus, AssetType, ConditionGrade, Sector
from app.services import capacity_service
from tests.conftest import make_asset


def test_only_functional_assets_count_towards_coverage():
    """The core of the module: a broken asset serves nobody."""
    broken = [
        make_asset("A1", "W1", AssetType.WATER_SUPPLY, status=AssetStatus.BROKEN),
        make_asset("A2", "W1", AssetType.WATER_SUPPLY, status=AssetStatus.NOT_STARTED),
        make_asset("A3", "W1", AssetType.WATER_SUPPLY, status=AssetStatus.UNDER_REPAIR),
        make_asset("A4", "W1", AssetType.WATER_SUPPLY, status=AssetStatus.PARTIAL),
    ]

    provision = capacity_service.summarise_provision(Sector.WATER.value, 10_000, broken)

    # One partial unit at half weight (750) plus one under-repair unit at a
    # quarter (375), against a nominal 1,500 people per unit.
    assert provision.served_population == 1_125
    assert provision.functional_asset_count == 0
    assert provision.asset_count == 4


def test_assets_from_other_sectors_are_ignored():
    assets = [
        make_asset("A1", "W1", AssetType.WATER_SUPPLY, units=10),
        make_asset("A2", "W1", AssetType.SCHOOL, units=50),
    ]

    water = capacity_service.summarise_provision(Sector.WATER.value, 10_000, assets)
    education = capacity_service.summarise_provision(Sector.EDUCATION.value, 10_000, assets)

    assert water.asset_count == 1
    assert education.asset_count == 1
    assert education.served_population == 60_000


def test_full_coverage_scores_no_absence():
    assets = [make_asset("A1", "W1", AssetType.WATER_SUPPLY, units=10)]

    provision = capacity_service.summarise_provision(Sector.WATER.value, 10_000, assets)

    assert provision.coverage_ratio >= 1.0


def test_old_asset_is_degraded_even_when_reported_good():
    """An inspector-reported GOOD asset that is 20 years old is still a liability."""
    old_good = make_asset("A1", "W1", AssetType.WATER_SUPPLY, installed_year=2000)
    new_good = make_asset("A2", "W1", AssetType.WATER_SUPPLY, installed_year=2024)

    assert capacity_service.is_degraded(old_good, 2025) is True
    assert capacity_service.is_degraded(new_good, 2025) is False


def test_poor_condition_is_degraded_regardless_of_age():
    asset = make_asset("A1", "W1", AssetType.WATER_SUPPLY,
                       condition=ConditionGrade.POOR, installed_year=2024)

    assert capacity_service.is_degraded(asset, 2025) is True


def test_quality_index_drops_with_condition_and_age():
    healthy = capacity_service.summarise_provision(
        Sector.WATER.value, 10_000,
        [make_asset("A1", "W1", AssetType.WATER_SUPPLY, installed_year=2024)],
        reference_year=2025,
    )
    degraded = capacity_service.summarise_provision(
        Sector.WATER.value, 10_000,
        [
            make_asset("A1", "W1", AssetType.WATER_SUPPLY, status=AssetStatus.BROKEN,
                       condition=ConditionGrade.CRITICAL, installed_year=2003),
            make_asset("A2", "W1", AssetType.WATER_SUPPLY, condition=ConditionGrade.POOR,
                       installed_year=2005),
        ],
        reference_year=2025,
    )

    assert degraded.quality_index < healthy.quality_index
    assert degraded.degraded_asset_count == 2


def test_empty_asset_set_has_no_quality_penalty():
    """No assets means a full absence gap, not a quality gap."""
    provision = capacity_service.summarise_provision(Sector.WATER.value, 10_000, [])

    assert provision.quality_index == 1.0
    assert provision.coverage_ratio == 0.0
    assert provision.asset_count == 0


def test_broken_asset_codes_are_reported_for_the_rationale():
    assets = [
        make_asset("A1", "W1", AssetType.BOREWELL, status=AssetStatus.BROKEN),
        make_asset("A2", "W1", AssetType.BOREWELL, status=AssetStatus.NOT_STARTED),
        make_asset("A3", "W1", AssetType.BOREWELL, status=AssetStatus.FUNCTIONAL),
    ]

    provision = capacity_service.summarise_provision(Sector.WATER.value, 10_000, assets)

    assert provision.broken_asset_codes == ["A1", "A2"]


def test_sector_helpers_are_consistent():
    assert capacity_service.asset_types_for(Sector.WATER.value) == ["WATER_SUPPLY", "BOREWELL"]
    assert Sector.UNKNOWN.value not in capacity_service.all_sectors()
    assert capacity_service.sector_coverage_targets()["HEALTH_CLINIC"] == 5_000


def test_sector_with_no_asset_mapping_scores_zero_coverage():
    """HOUSING has no physical mapping, so it must not silently look covered."""
    provision = capacity_service.summarise_provision(Sector.HOUSING.value, 10_000, [])

    assert provision.coverage_ratio == 0.0
    assert provision.asset_types == []
