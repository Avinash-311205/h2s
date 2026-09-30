"""Investment analytics: absorption, delays and stall detection."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.enums import ProjectStatus, Sector
from app.services import investment_service
from tests.conftest import make_project


def test_overdue_unfinished_project_is_delayed():
    project = make_project("P1", "W", sanctioned_days_ago=800, duration_months=6)

    assert investment_service.is_delayed(project, date.today()) is True


def test_completed_project_is_never_delayed():
    project = make_project(
        "P1", "W", status=ProjectStatus.COMPLETED, sanctioned_days_ago=800, duration_months=6
    )

    assert investment_service.is_delayed(project, date.today()) is False


def test_cancelled_project_is_never_delayed():
    """A cancelled project is a decision, not a delivery failure."""
    project = make_project(
        "P1", "W", status=ProjectStatus.CANCELLED, sanctioned_days_ago=800, duration_months=6
    )

    assert investment_service.is_delayed(project, date.today()) is False


def test_fallback_to_recorded_delay_when_dates_are_missing():
    project = make_project("P1", "W", delay_days=45)
    project.sanctioned_on = None
    project.expected_completion_on = None

    assert investment_service.is_delayed(project, date.today()) is True


def test_sanctioned_but_unspent_project_is_a_stall():
    """The classic failure mode: budget line exists, nothing was built."""
    project = make_project(
        "P1", "W", status=ProjectStatus.PLANNED,
        budget=1_000.0, spent=40.0, sanctioned_days_ago=400, duration_months=12,
    )

    assert investment_service.is_absorption_stalled(project, date.today()) is True


def test_well_absorbed_project_is_not_a_stall():
    project = make_project(
        "P1", "W", status=ProjectStatus.IN_PROGRESS,
        budget=1_000.0, spent=800.0, sanctioned_days_ago=400, duration_months=12,
    )

    assert investment_service.is_absorption_stalled(project, date.today()) is False


def test_early_project_with_low_spend_is_not_yet_a_stall():
    """Low spend early in a project is expected, not a failure."""
    project = make_project(
        "P1", "W", status=ProjectStatus.IN_PROGRESS,
        budget=1_000.0, spent=10.0, sanctioned_days_ago=30, duration_months=12,
    )

    assert investment_service.elapsed_fraction(project, date.today()) < 0.5
    assert investment_service.is_absorption_stalled(project, date.today()) is False


def test_completed_project_is_never_stalled():
    project = make_project(
        "P1", "W", status=ProjectStatus.COMPLETED, budget=1_000.0, spent=1_000.0
    )

    assert investment_service.is_absorption_stalled(project, date.today()) is False


def test_summary_aggregates_portfolio_health():
    projects = [
        make_project("P1", "W", status=ProjectStatus.COMPLETED, budget=100.0, spent=100.0),
        make_project("P2", "W", status=ProjectStatus.IN_PROGRESS, budget=200.0, spent=150.0),
        make_project("P3", "W", status=ProjectStatus.DELAYED, budget=300.0, spent=90.0),
        make_project("P4", "W", status=ProjectStatus.CANCELLED, budget=400.0, spent=120.0),
    ]

    summary = investment_service.summarise_investment(projects)

    assert summary.project_count == 4
    assert summary.sanctioned_lakhs == 1_000.0
    assert summary.spent_lakhs == 460.0
    assert summary.absorption_rate == pytest.approx(0.46, abs=0.001)
    assert summary.completed_count == 1
    assert summary.cancelled_count == 1
    assert summary.active_count == 2


def test_stalled_projects_are_ranked_by_unspent_money():
    projects = [
        make_project("SMALL", "W", status=ProjectStatus.PLANNED, budget=100.0, spent=5.0,
                     sanctioned_days_ago=400, duration_months=12),
        make_project("BIG", "W", status=ProjectStatus.PLANNED, budget=900.0, spent=20.0,
                     sanctioned_days_ago=400, duration_months=12),
    ]

    summary = investment_service.summarise_investment(projects)

    assert [row["project_code"] for row in summary.stalled_projects] == ["BIG", "SMALL"]
    assert summary.stalled_projects[0]["spend_ratio"] == pytest.approx(20 / 900, abs=0.001)


def test_empty_portfolio_has_zero_delivery_score_penalty():
    summary = investment_service.summarise_investment([])

    assert summary.project_count == 0
    assert summary.absorption_rate == 0.0
    # Nothing was sanctioned, so nothing is failing delivery.
    assert investment_service.delivery_score(summary) == 1.0


def test_delivery_score_falls_as_completion_and_absorption_drop():
    healthy = investment_service.summarise_investment([
        make_project("P1", "W", status=ProjectStatus.COMPLETED, budget=100.0, spent=100.0),
    ])
    unhealthy = investment_service.summarise_investment([
        make_project("P1", "W", status=ProjectStatus.PLANNED, budget=100.0, spent=2.0),
    ])

    assert investment_service.delivery_score(healthy) > investment_service.delivery_score(unhealthy)
    assert 0.0 <= investment_service.delivery_score(unhealthy) <= 1.0


def test_elapsed_fraction_is_clamped_to_the_project_window():
    early = make_project("P1", "W", sanctioned_days_ago=10, duration_months=12)
    late = make_project("P2", "W", sanctioned_days_ago=2_000, duration_months=12)

    assert investment_service.elapsed_fraction(early, date.today()) == pytest.approx(0.03, abs=0.05)
    assert investment_service.elapsed_fraction(late, date.today()) == 1.0


def test_zero_budget_project_is_not_a_stall():
    project = make_project("P1", "W", budget=0.0, spent=0.0, sanctioned_days_ago=400)

    assert investment_service.is_absorption_stalled(project, date.today()) is False


def test_projects_with_no_dates_are_treated_as_fully_elapsed():
    project = make_project("P1", "W", budget=100.0, spent=0.0)
    project.sanctioned_on = None
    project.expected_completion_on = None

    assert investment_service.elapsed_fraction(project, date.today()) == 1.0
    assert investment_service.is_absorption_stalled(project, date.today()) is True


def test_summary_as_dict_is_json_ready():
    summary = investment_service.summarise_investment([
        make_project("P1", "W", sector=Sector.WATER, status=ProjectStatus.IN_PROGRESS,
                     budget=100.0, spent=50.0),
    ])

    payload = summary.as_dict()
    assert payload["absorption_rate"] == 0.5
    assert isinstance(payload["stalled_projects"], list)


def test_expected_completion_in_the_future_is_not_a_delay():
    project = make_project(
        "P1", "W", sanctioned_days_ago=10, duration_months=12
    )
    assert project.expected_completion_on > date.today()
    assert investment_service.is_delayed(project, date.today()) is False


def test_delay_days_are_averaged_across_delayed_projects_only():
    projects = [
        make_project("P1", "W", status=ProjectStatus.DELAYED, sanctioned_days_ago=800,
                     duration_months=6, delay_days=60),
        make_project("P2", "W", status=ProjectStatus.DELAYED, sanctioned_days_ago=900,
                     duration_months=6, delay_days=120),
        make_project("P3", "W", status=ProjectStatus.COMPLETED, delay_days=999),
    ]

    summary = investment_service.summarise_investment(projects)

    assert summary.mean_delay_days == pytest.approx(90.0, abs=0.01)


def test_stalled_detection_uses_the_thirty_five_percent_threshold():
    just_under = make_project("P1", "W", status=ProjectStatus.IN_PROGRESS, budget=100.0,
                              spent=34.0, sanctioned_days_ago=400, duration_months=12)
    just_over = make_project("P2", "W", status=ProjectStatus.IN_PROGRESS, budget=100.0,
                             spent=36.0, sanctioned_days_ago=400, duration_months=12)

    assert investment_service.is_absorption_stalled(just_under, date.today()) is True
    assert investment_service.is_absorption_stalled(just_over, date.today()) is False


def test_projected_completion_date_mismatch_uses_supplied_reference_date():
    project = make_project("P1", "W", sanctioned_days_ago=400, duration_months=6)

    assert investment_service.is_delayed(project, date.today()) is True
    # A reference date before the due date means the project is not yet late.
    assert investment_service.is_delayed(project, project.expected_completion_on) is False