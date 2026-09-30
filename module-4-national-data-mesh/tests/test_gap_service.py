"""Gap analysis: ranking, band thresholds, explainability and action choice."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.enums import AssetStatus, AssetType, ConditionGrade, GapSeverity, ProjectStatus, Sector
from app.core.config import settings
from app.services import gap_service
from tests.conftest import make_asset, make_demand, make_project, make_ward


def compute(**overrides):
    """Compute one gap with sensible defaults, overridable per test."""
    kwargs = {
        "ward_code": "CHN-01",
        "sector": Sector.WATER.value,
        "population": 20_000,
        "demand_rows": [],
        "assets": [],
        "projects": [],
        "reference_year": date.today().year,
        "demand_reference": 100,
    }
    kwargs.update(overrides)
    return gap_service.compute_gap(**kwargs)


# --- scoring -----------------------------------------------------------------
def test_unserved_ward_with_maximum_demand_is_critical():
    """Maximum volume, maximum severity and no assets: the top of the scale."""
    result = compute(
        population=30_000,
        assets=[],
        demand_rows=[make_demand("CHN-01", Sector.WATER, complaints=100, avg_severity=5.0, critical=9)],
        demand_reference=100,
    )

    assert result.gap_score == pytest.approx(75.0, abs=0.01)
    assert result.severity == GapSeverity.CRITICAL.value
    assert result.recommended_action == "BUILD_INFRASTRUCTURE"


def test_slightly_lower_severity_keeps_the_same_ward_out_of_critical():
    """Severity 4.5 pulls demand to 0.95, which lands in HIGH rather than CRITICAL."""
    result = compute(
        population=30_000,
        assets=[],
        demand_rows=[make_demand("CHN-01", Sector.WATER, complaints=100, avg_severity=4.5, critical=9)],
        demand_reference=100,
    )

    assert result.severity == GapSeverity.HIGH.value


def test_well_covered_quiet_ward_has_no_gap():
    result = compute(
        population=20_000,
        assets=[make_asset("A1", "CHN-01", AssetType.WATER_SUPPLY, units=20)],
        demand_rows=[make_demand("CHN-01", Sector.WATER, complaints=2, avg_severity=1.2)],
    )

    assert result.gap_score < gap_service.MODERATE_SCORE
    assert result.severity == GapSeverity.LOW.value
    assert result.coverage_ratio >= 1.0


def test_silent_under_served_ward_still_scores_on_absence():
    """Nobody complained, but there is not enough water infrastructure."""
    result = compute(
        population=40_000,
        assets=[],
        demand_rows=[],
    )

    # No demand (0) but full absence (1.0): 100 * 0.35 * 1.0 = 35.
    assert result.demand_score == 0.0
    assert result.absence_score == 1.0
    assert result.gap_score == pytest.approx(35.0, abs=0.01)
    assert result.severity == GapSeverity.MODERATE.value


def test_demand_component_is_normalised_against_the_dataset_maximum():
    """A locally noisy ward must not outrank a globally worse one."""
    loud = compute(demand_rows=[make_demand("W", complaints=100, avg_severity=5.0)], demand_reference=100)
    quiet = compute(demand_rows=[make_demand("W", complaints=25, avg_severity=5.0)], demand_reference=100)

    assert loud.demand_score > quiet.demand_score
    assert quiet.demand_score == pytest.approx(0.6 * 0.25 + 0.4 * 1.0, abs=0.001)


def test_severity_one_complaints_contribute_no_demand():
    result = compute(demand_rows=[make_demand("W", complaints=100, avg_severity=1.0)], demand_reference=100)

    assert result.demand_score == pytest.approx(0.6, abs=0.001)


def test_severity_five_complaints_max_out_the_severity_term():
    result = compute(demand_rows=[make_demand("W", complaints=100, avg_severity=5.0)], demand_reference=100)

    assert result.demand_score == pytest.approx(1.0, abs=0.001)


def test_scores_are_bounded_zero_to_one_hundred():
    extreme = compute(
        population=100_000,
        assets=[
            make_asset("A1", "W", AssetType.WATER_SUPPLY, status=AssetStatus.BROKEN,
                       condition=ConditionGrade.CRITICAL, installed_year=1990),
        ],
        demand_rows=[make_demand("W", complaints=999, avg_severity=5.0, critical=99)],
    )

    assert 0.0 <= extreme.demand_score <= 1.0
    assert 0.0 <= extreme.absence_score <= 1.0
    assert 0.0 <= extreme.quality_score <= 1.0
    assert 0.0 <= extreme.gap_score <= 100.0


def test_zero_population_ward_does_not_divide_by_zero():
    result = compute(population=0, assets=[], demand_rows=[])

    assert result.gap_score >= 0.0
    assert result.coverage_ratio == 0.0


# --- bands -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, GapSeverity.LOW),
        (34.99, GapSeverity.LOW),
        (35.0, GapSeverity.MODERATE),
        (54.99, GapSeverity.MODERATE),
        (55.0, GapSeverity.HIGH),
        (74.99, GapSeverity.HIGH),
        (75.0, GapSeverity.CRITICAL),
        (100.0, GapSeverity.CRITICAL),
    ],
)
def test_band_thresholds(score, expected):
    assert gap_service.band_for_score(score) == expected.value


# --- actions -----------------------------------------------------------------
def test_broken_assets_with_critical_complaints_prompt_urgent_repair():
    result = compute(
        population=1_000,
        assets=[make_asset("A1", "W", AssetType.WATER_SUPPLY, status=AssetStatus.BROKEN,
                           condition=ConditionGrade.CRITICAL)],
        demand_rows=[make_demand("W", complaints=30, avg_severity=4.0, critical=3)],
    )

    assert result.recommended_action == "URGENT_REPAIR"
    assert "not functional" in result.rationale


def test_covered_but_noisy_network_prompts_quality_work():
    result = compute(
        population=5_000,
        assets=[make_asset("A1", "W", AssetType.WATER_SUPPLY, units=5, installed_year=2019)],
        demand_rows=[make_demand("W", complaints=95, avg_severity=4.6, critical=2)],
    )

    assert result.recommended_action in {"ACCELERATE_PROJECTS", "IMPROVE_QUALITY"}
    assert result.coverage_ratio >= 1.0


def test_active_projects_are_not_flagged_for_repair():
    result = compute(
        population=5_000,
        assets=[make_asset("A1", "W", AssetType.WATER_SUPPLY, units=5)],
        demand_rows=[make_demand("W", complaints=1, avg_severity=1.0)],
        projects=[make_project("P1", "W", Sector.WATER, status=ProjectStatus.IN_PROGRESS)],
    )

    assert result.recommended_action == "MONITOR"
    assert result.active_project_count == 1


# --- investment context ------------------------------------------------------
def test_cancelled_project_capex_is_excluded():
    """Money spent on a cancelled project bought no capacity."""
    projects = [
        make_project("P1", "W", Sector.WATER, status=ProjectStatus.CANCELLED, budget=900.0, spent=450.0),
        make_project("P2", "W", Sector.WATER, status=ProjectStatus.IN_PROGRESS, budget=100.0, spent=90.0),
    ]

    result = compute(projects=projects)

    assert result.committed_capex_lakhs == 100.0
    assert result.spent_capex_lakhs == 90.0


def test_other_sector_projects_are_not_counted():
    projects = [
        make_project("P1", "W", Sector.ROAD, budget=5_000.0, spent=5_000.0),
        make_project("P2", "W", Sector.WATER, budget=200.0, spent=200.0),
    ]

    result = compute(projects=projects)

    assert result.committed_capex_lakhs == 200.0


def test_evidence_explains_the_score():
    result = compute(
        assets=[make_asset("A1", "W", AssetType.WATER_SUPPLY, status=AssetStatus.BROKEN,
                           condition=ConditionGrade.CRITICAL, installed_year=2001)],
        demand_rows=[make_demand("W", complaints=40, avg_severity=4.0, critical=5)],
    )

    assert result.evidence["broken_asset_codes"] == ["A1"]
    assert result.evidence["critical_complaints"] == 5
    assert result.evidence["sector_demand_reference"] == 100
    assert result.evidence["mean_asset_age_years"] > 0


def test_as_dict_is_json_ready_and_rounded():
    result = compute(demand_rows=[make_demand("W", complaints=33, avg_severity=3.33)])
    payload = result.as_dict()

    assert set(payload["components"]) == {"demand", "absence", "quality"}
    assert isinstance(payload["evidence"], dict)
    assert payload["gap_score"] == round(payload["gap_score"], 2)


# --- whole-mesh run ----------------------------------------------------------
def test_run_orders_wards_by_severity_and_covers_all_sectors():
    wards = [
        make_ward("CHN-01", population=30_000, lat=13.08, lon=80.27),
        make_ward("CHN-02", population=25_000, lat=13.13, lon=80.27),
    ]
    assets_by_ward = {
        "CHN-01": [],
        "CHN-02": [make_asset("A1", "CHN-02", AssetType.WATER_SUPPLY, units=25)],
    }
    demand_by_ward = {
        "CHN-01": [make_demand("CHN-01", Sector.WATER, complaints=120, avg_severity=4.4, critical=8)],
        "CHN-02": [make_demand("CHN-02", Sector.WATER, complaints=3, avg_severity=1.3)],
    }

    results = gap_service.run_gap_analysis(
        wards=wards,
        demand_by_ward=demand_by_ward,
        assets_by_ward=assets_by_ward,
        projects_by_ward={},
    )

    assert len(results) == len(wards) * len(gap_service.sector_defaults())
    assert results == sorted(results, key=lambda r: r.gap_score, reverse=True)
    assert results[0].ward_code == "CHN-01"


def test_run_can_be_restricted_to_selected_sectors():
    wards = [make_ward("CHN-01")]

    results = gap_service.run_gap_analysis(
        wards=wards,
        demand_by_ward={},
        assets_by_ward={},
        projects_by_ward={},
        sectors=[Sector.WATER.value, Sector.ROAD.value],
    )

    assert {result.sector for result in results} == {"WATER", "ROAD"}


def test_run_skips_unknown_sectors():
    results = gap_service.run_gap_analysis(
        wards=[make_ward("CHN-01")],
        demand_by_ward={},
        assets_by_ward={},
        projects_by_ward={},
        sectors=[Sector.UNKNOWN.value],
    )

    assert results == []


def test_demand_is_normalised_per_sector_not_globally():
    """A noisy water ward must not silence every digital complaint in the mesh.

    CHN-01 has 300 water complaints and 4 digital ones; CHN-02 has 10 water and
    2 digital. With a global reference the digital ward would look silent, so
    each sector is normalised against its own busiest ward instead.
    """
    wards = [make_ward("CHN-01"), make_ward("CHN-02", lat=13.13)]
    demand_by_ward = {
        "CHN-01": [
            make_demand("CHN-01", Sector.WATER, complaints=300, avg_severity=5.0),
            make_demand("CHN-01", Sector.DIGITAL_CONNECTIVITY, complaints=4, avg_severity=5.0),
        ],
        "CHN-02": [
            make_demand("CHN-02", Sector.WATER, complaints=10, avg_severity=5.0),
            make_demand("CHN-02", Sector.DIGITAL_CONNECTIVITY, complaints=2, avg_severity=5.0),
        ],
    }

    results = gap_service.run_gap_analysis(
        wards=wards,
        demand_by_ward=demand_by_ward,
        assets_by_ward={},
        projects_by_ward={},
        sectors=[Sector.WATER.value, Sector.DIGITAL_CONNECTIVITY.value],
    )
    index = {(r.ward_code, r.sector): r for r in results}

    assert index[("CHN-02", "WATER")].demand_score == pytest.approx(0.6 * (10 / 300) + 0.4, abs=0.001)
    assert index[("CHN-02", "DIGITAL_CONNECTIVITY")].demand_score == pytest.approx(0.6 * 0.5 + 0.4, abs=0.001)
    assert index[("CHN-01", "DIGITAL_CONNECTIVITY")].evidence["sector_demand_reference"] == 4


def test_explicit_demand_reference_overrides_the_per_sector_maximum():
    results = gap_service.run_gap_analysis(
        wards=[make_ward("CHN-01")],
        demand_by_ward={"CHN-01": [make_demand("CHN-01", complaints=50, avg_severity=5.0)]},
        assets_by_ward={},
        projects_by_ward={},
        sectors=[Sector.WATER.value],
        demand_reference=100,
    )

    assert results[0].demand_score == pytest.approx(0.6 * 0.5 + 0.4, abs=0.001)


def test_sector_demand_references_are_independent():
    references = gap_service.sector_demand_references(
        {
            "A": [make_demand("A", Sector.WATER, complaints=10),
                  make_demand("A", Sector.ROAD, complaints=70)],
            "B": [make_demand("B", Sector.WATER, complaints=25)],
        },
        [Sector.WATER.value, Sector.ROAD.value],
    )

    assert references == {"WATER": 25, "ROAD": 70}


def test_complaints_from_other_sectors_do_not_leak_into_a_gap():
    """A ward complaining about water must not look like it has a road problem."""
    result = compute(
        sector=Sector.WATER.value,
        population=20_000,
        assets=[make_asset("A1", "W", AssetType.WATER_SUPPLY, units=20)],
        demand_rows=[
            make_demand("W", Sector.WATER, complaints=4, avg_severity=1.5),
            make_demand("W", Sector.ELECTRICITY, complaints=400, avg_severity=5.0, critical=50),
        ],
        demand_reference=400,
    )

    assert result.complaints == 4
    assert result.avg_severity == 1.5
    # Only the water row's single critical complaint; the electricity row's 50
    # must not leak across.
    assert result.evidence["critical_complaints"] == 1


def test_sector_without_a_capacity_metric_contributes_no_absence():
    """A road has no people-per-unit figure, so absence must not read as 100%."""
    result = compute(
        sector=Sector.ROAD.value,
        population=30_000,
        assets=[make_asset("R1", "W", AssetType.ROAD, units=40, installed_year=2023)],
        demand_rows=[],
    )

    assert result.absence_score == 0.0
    assert result.evidence["absence_measurable"] is False
    # No complaints and no measurable absence, so only a token quality term is
    # left - far short of even a MODERATE band.
    assert result.gap_score < 2.0
    assert result.severity == GapSeverity.LOW.value


def test_measurable_sector_reports_the_flag():
    result = compute(assets=[make_asset("A1", "W", AssetType.WATER_SUPPLY, units=30)])

    assert result.evidence["absence_measurable"] is True
    assert result.absence_score == 0.0


def test_unmappable_sector_is_not_scored_in_a_full_run():
    """Housing has no asset register, so no ward should get a phantom gap."""
    sectors = gap_service.sector_defaults()

    assert Sector.HOUSING.value not in sectors
    assert Sector.PUBLIC_SAFETY.value not in sectors
    assert Sector.UNKNOWN.value not in sectors


def test_empty_mesh_returns_no_gaps():
    assert gap_service.run_gap_analysis(
        wards=[], demand_by_ward={}, assets_by_ward={}, projects_by_ward={}
    ) == []


def test_weights_come_from_settings_and_sum_to_one():
    assert sum(settings.gap_weights.values()) == pytest.approx(1.0)
    assert settings.gap_weights == {"demand": 0.40, "absence": 0.35, "quality": 0.25}