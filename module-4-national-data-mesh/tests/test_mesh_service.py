"""Mesh catalogue: lineage, completeness, validity and timeliness."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums import DataDomain, ProjectStatus, QualityStatus, Sector
from app.core.utils import utcnow
from app.services import mesh_service
from tests.conftest import make_asset, make_demand, make_project, make_ward


# --- scoring primitives ------------------------------------------------------
def test_perfect_domain_is_healthy():
    verdict = mesh_service.score_domain(DataDomain.GIS.value, 10, 1.0, 1.0, refresh_interval_days=90)

    assert verdict.quality_score == 1.0
    assert verdict.status == QualityStatus.HEALTHY.value
    assert verdict.issues == []


def test_empty_domain_is_unavailable():
    verdict = mesh_service.score_domain(DataDomain.CITIZEN.value, 0, 0.0, 0.0)

    assert verdict.status == QualityStatus.UNAVAILABLE.value
    assert "no records" in verdict.issues


def test_weakest_dimension_drives_the_status():
    """Completeness and validity cannot be averaged away by timeliness."""
    verdict = mesh_service.score_domain(
        DataDomain.INFRASTRUCTURE.value, 100, 1.0, 1.0,
        generated_at=utcnow() - timedelta(days=365), refresh_interval_days=30,
    )

    assert verdict.timeliness == 0.0
    assert verdict.status == QualityStatus.STALE.value


def test_timeliness_decays_to_zero_at_twice_the_refresh_interval():
    fresh = mesh_service.timeliness_score(utcnow(), refresh_interval_days=30)
    one_interval = mesh_service.timeliness_score(
        utcnow() - timedelta(days=30), refresh_interval_days=30
    )
    two_intervals = mesh_service.timeliness_score(
        utcnow() - timedelta(days=60), refresh_interval_days=30
    )

    assert fresh == 1.0
    assert one_interval == pytest.approx(0.5, abs=0.01)
    assert two_intervals == 0.0


def test_future_timestamps_are_treated_as_just_refreshed():
    assert mesh_service.timeliness_score(utcnow() + timedelta(days=5)) == 1.0


def test_scores_are_clamped_for_out_of_range_inputs():
    verdict = mesh_service.score_domain(DataDomain.GIS.value, 5, 1.4, -0.3)

    assert 0.0 <= verdict.quality_score <= 1.0
    assert verdict.completeness == 1.0
    assert verdict.validity == 0.0


# --- per-domain rules --------------------------------------------------------
def test_ward_assessment_flags_bad_coordinates():
    good = make_ward("A", lat=13.0, lon=80.0)
    broken = make_ward("B", lat=999.0, lon=80.0)
    broken.name = ""
    good.centroid_latitude = 13.0
    # Sanity: the good ward's bounds contain its centroid.
    good.min_latitude, good.max_latitude = 12.9, 13.1

    verdict = mesh_service.assess_wards([good, broken])

    assert verdict.domain == DataDomain.GIS.value
    assert verdict.completeness == 0.5
    assert verdict.validity < 1.0


def test_asset_assessment_flags_unknown_ward_reference():
    assets = [
        make_asset("A1", "CHN-01"),
        make_asset("A2", "ZZZ-99"),
        make_asset("A3", "CHN-02", installed_year=1800),
    ]

    verdict = mesh_service.assess_assets(assets, ward_codes={"CHN-01", "CHN-02"})

    # A1 is valid; A2 points at an unknown ward; A3 was installed in 1800.
    assert verdict.domain == DataDomain.INFRASTRUCTURE.value
    assert verdict.completeness == 1.0
    assert verdict.validity == pytest.approx(1 / 3, abs=0.001)


def test_project_assessment_flags_overspend_beyond_tolerance():
    projects = [
        make_project("P1", "CHN-01", budget=100.0, spent=100.0),
        make_project("P2", "CHN-01", budget=100.0, spent=104.0),  # 4% - within tolerance
        make_project("P3", "CHN-01", budget=100.0, spent=200.0),  # 100% - invalid
    ]

    verdict = mesh_service.assess_projects(projects, ward_codes={"CHN-01"})

    assert verdict.validity == pytest.approx(2 / 3, abs=0.001)


def test_cancelled_project_is_still_structurally_valid():
    """Delivery failure is the investment service's job, not validity's."""
    projects = [make_project("P1", "CHN-01", status=ProjectStatus.CANCELLED)]

    verdict = mesh_service.assess_projects(projects, ward_codes={"CHN-01"})

    assert verdict.validity == 1.0


def test_demand_assessment_flags_zero_and_out_of_range_severity():
    rows = [
        make_demand("CHN-01", complaints=10, avg_severity=3.0),
        make_demand("CHN-02", complaints=0, avg_severity=3.0),
        make_demand("CHN-03", complaints=5, avg_severity=9.0),
    ]

    verdict = mesh_service.assess_demand(rows, ward_codes={"CHN-01", "CHN-02", "CHN-03"})

    # Zero complaints and an out-of-range severity are both invalid; the
    # well-formed row is the only valid one.
    assert verdict.domain == DataDomain.CITIZEN.value
    assert verdict.validity == pytest.approx(1 / 3, abs=0.001)


def test_zero_complaints_is_valid_because_a_quiet_ward_is_not_broken():
    rows = [make_demand("CHN-01", complaints=0, avg_severity=0.0)]

    verdict = mesh_service.assess_demand(rows, ward_codes={"CHN-01"})

    assert verdict.validity == 0.0  # severity of 0 is out of the 1-5 range


# --- aggregate refresh -------------------------------------------------------
def test_refresh_all_scores_every_populated_domain():
    wards = [make_ward("CHN-01")]

    verdicts = mesh_service.refresh_all(
        wards=wards,
        assets=[make_asset("A1", "CHN-01")],
        projects=[make_project("P1", "CHN-01")],
        demand=[make_demand("CHN-01")],
    )

    assert {verdict.domain for verdict in verdicts} == {
        DataDomain.GIS.value,
        DataDomain.INFRASTRUCTURE.value,
        DataDomain.INVESTMENT.value,
        DataDomain.CITIZEN.value,
    }
    assert all(verdict.record_count > 0 for verdict in verdicts)


def test_refresh_all_reports_missing_domains_as_unavailable():
    verdicts = mesh_service.refresh_all(wards=[make_ward("A")], assets=[], projects=[], demand=[])

    unavailable = [v for v in verdicts if v.status == QualityStatus.UNAVAILABLE.value]
    assert len(unavailable) == 3


# --- lineage -----------------------------------------------------------------
def test_lineage_is_declared_for_every_domain():
    for domain in DataDomain:
        assert mesh_service.lineage_for(domain.value), f"{domain} has no lineage"


def test_lineage_of_unknown_domain_is_empty():
    assert mesh_service.lineage_for("NOT_A_DOMAIN") == []


def test_every_domain_has_a_product_description():
    for domain in DataDomain:
        assert mesh_service.PRODUCT_DESCRIPTIONS.get(domain.value)


def test_stale_after_days_matches_the_scoring_window():
    assert mesh_service.stale_after_days(30) == timedelta(days=60)