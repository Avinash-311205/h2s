"""Tests for emerging-risk rules.

Each rule is a claim about what deserves a district officer's attention, so the
tests pin down both when a rule fires and - more importantly - when it must not.
A risk system that cries wolf is worse than none.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.core.enums import ProjectStatus
from app.models.intelligence_tables import DemandWindow, GapSnapshot, ProjectSnapshot
from app.services.risk_service import (
    confirmatory_signals,
    detect_critical_concentration,
    detect_deteriorating_with_spend,
    detect_new_category,
    detect_risks,
    detect_spike,
    detect_stalled_absorption,
    risk_code_for,
    summarise_risks,
)


def demand(ward_code="W1", sector="WATER", count=20, *, end_days_ago=0, district="Chennai"):
    end = datetime(2026, 6, 30) - timedelta(days=end_days_ago)
    return DemandWindow(
        ward_code=ward_code,
        district=district,
        sector=sector,
        window_days=30,
        window_start=end - timedelta(days=30),
        window_end=end,
        complaint_count=count,
        critical_count=0,
        avg_severity=3.0,
        population=10_000,
    )


def gap(ward_code="W1", sector="WATER", score=80.0, severity="CRITICAL", days_ago=0):
    return GapSnapshot(
        ward_code=ward_code,
        sector=sector,
        snapshot_at=datetime(2026, 6, 30) - timedelta(days=days_ago),
        gap_score=score,
        severity=severity,
    )


def project(code="P1", ward_code="W1", sector="WATER", status="IN_PROGRESS", budget=1000.0, spent=100.0):
    return ProjectSnapshot(
        project_code=code,
        ward_code=ward_code,
        sector=sector,
        status=status,
        title=f"{sector} works",
        budget_lakhs=budget,
        spent_lakhs=spent,
        sanctioned_on=datetime(2025, 1, 1),
        expected_completion_on=datetime(2026, 12, 31),
        snapshot_at=datetime(2026, 6, 30),
    )


class TestRiskCode:
    def test_code_is_stable_and_readable(self):
        assert risk_code_for("SPIKE", "W1", "WATER") == "SPIKE:W1:WATER"

    def test_city_wide_signals_fall_back_to_placeholders(self):
        assert risk_code_for("SPIKE", None, None) == "SPIKE:CITY:ALL"

    def test_the_same_inputs_always_produce_the_same_code(self):
        # Re-running detection must update a risk, never duplicate it.
        assert risk_code_for("SPIKE", "W1", "WATER") == risk_code_for("SPIKE", "W1", "WATER")


class TestSpikeRule:
    def test_a_triple_jump_above_the_floor_fires(self):
        signal = detect_spike("W1", "Chennai", "WATER", 12, 60)
        assert signal is not None
        assert signal.risk_type == "SPIKE"
        assert signal.evidence["observed_ratio"] == pytest.approx(5.0)

    def test_a_small_absolute_move_does_not_fire(self):
        # 1 -> 3 is a doubling and meaningless; the absolute floor is what stops
        # it from looking like a spike.
        assert detect_spike("W1", "Chennai", "WATER", 1, 3) is None

    def test_a_proportional_jump_below_the_floor_does_not_fire(self):
        assert detect_spike("W1", "Chennai", "WATER", 4, 9) is None

    def test_a_modest_rise_does_not_fire(self):
        assert detect_spike("W1", "Chennai", "WATER", 50, 70) is None

    def test_a_fall_is_not_a_spike(self):
        assert detect_spike("W1", "Chennai", "WATER", 100, 5) is None

    def test_from_zero_is_a_new_category_not_a_spike(self):
        # Division by zero here would produce an infinite ratio.
        assert detect_spike("W1", "Chennai", "WATER", 0, 100) is None

    def test_a_large_ratio_is_critical_and_a_modest_one_is_medium(self):
        assert detect_spike("W1", "Chennai", "WATER", 12, 100).severity == "CRITICAL"
        assert detect_spike("W1", "Chennai", "WATER", 12, 30).severity == "MEDIUM"

    def test_confidence_rises_with_the_size_of_the_jump(self):
        modest = detect_spike("W1", "Chennai", "WATER", 12, 30)
        extreme = detect_spike("W1", "Chennai", "WATER", 12, 200)
        assert extreme.confidence > modest.confidence

    def test_thresholds_are_configurable(self):
        assert detect_spike("W1", "Chennai", "WATER", 10, 15, ratio_threshold=1.2,
                            min_complaints=5) is not None


class TestNewCategoryRule:
    def test_silent_to_loud_fires(self):
        signal = detect_new_category("W1", "Chennai", "WATER", 0, 8)
        assert signal is not None
        assert signal.risk_type == "NEW_CATEGORY"

    def test_a_ward_that_always_had_complaints_is_not_new(self):
        assert detect_new_category("W1", "Chennai", "WATER", 5, 8) is None

    def test_too_few_complaints_is_not_worth_flagging(self):
        assert detect_new_category("W1", "Chennai", "WATER", 0, 1) is None

    def test_staying_silent_is_not_a_new_problem(self):
        assert detect_new_category("W1", "Chennai", "WATER", 0, 0) is None


class TestDeterioratingWithSpendRule:
    def test_worsening_despite_active_money_fires(self):
        signal = detect_deteriorating_with_spend(
            "W1", "Chennai", "WATER", previous=20, recent=50, active_projects=2,
            committed_lakhs=800.0, spent_lakhs=400.0,
        )
        assert signal is not None
        assert signal.risk_type == "DETERIORATING_WITH_SPEND"

    def test_improving_despite_money_does_not_fire(self):
        assert detect_deteriorating_with_spend(
            "W1", "Chennai", "WATER", previous=50, recent=20, active_projects=2,
            committed_lakhs=800.0, spent_lakhs=400.0,
        ) is None

    def test_no_active_projects_is_a_different_problem(self):
        # Unfunded gaps are Module 4's subject, not this rule's.
        assert detect_deteriorating_with_spend(
            "W1", "Chennai", "WATER", previous=20, recent=50, active_projects=0,
            committed_lakhs=0.0, spent_lakhs=0.0,
        ) is None

    def test_a_change_below_the_threshold_does_not_fire(self):
        assert detect_deteriorating_with_spend(
            "W1", "Chennai", "WATER", previous=100, recent=105, active_projects=1,
            committed_lakhs=500.0, spent_lakhs=250.0,
        ) is None

    def test_nearly_unspent_money_makes_it_critical(self):
        signal = detect_deteriorating_with_spend(
            "W1", "Chennai", "WATER", previous=20, recent=90, active_projects=1,
            committed_lakhs=800.0, spent_lakhs=40.0,
        )
        assert signal.severity == "CRITICAL"


class TestStalledAbsorptionRule:
    def test_past_half_its_window_with_no_spend_fires(self):
        assert detect_stalled_absorption(project(), elapsed_fraction=0.6) is not None

    def test_early_in_the_window_it_is_too_soon_to_judge(self):
        # A project sanctioned last month has not had a chance to spend yet.
        assert detect_stalled_absorption(project(), elapsed_fraction=0.1) is None

    def test_spending_proportionately_does_not_fire(self):
        assert detect_stalled_absorption(project(spent=800.0), elapsed_fraction=0.9) is None

    def test_a_completed_project_is_not_at_risk(self):
        assert detect_stalled_absorption(
            project(status=ProjectStatus.COMPLETED.value), elapsed_fraction=0.9
        ) is None

    def test_a_cancelled_project_is_not_at_risk(self):
        assert detect_stalled_absorption(
            project(status=ProjectStatus.CANCELLED.value), elapsed_fraction=0.9
        ) is None

    def test_a_zero_budget_project_is_not_judged_on_absorption(self):
        assert detect_stalled_absorption(project(budget=0.0, spent=0.0), elapsed_fraction=0.9) is None

    def test_a_large_shortfall_is_high_severity(self):
        signal = detect_stalled_absorption(project(budget=2000.0, spent=50.0), elapsed_fraction=0.9)
        assert signal.severity == "HIGH"

    def test_evidence_records_the_unspent_amount(self):
        signal = detect_stalled_absorption(project(budget=1000.0, spent=100.0), elapsed_fraction=0.9)
        assert signal.evidence["unspent_lakhs"] == pytest.approx(900.0)


class TestCriticalConcentrationRule:
    def test_a_critical_gap_that_is_still_rising_fires(self):
        signal = detect_critical_concentration(
            "W1", "Chennai", "WATER", gap_score=88.0, gap_severity="CRITICAL",
            previous=20, recent=40,
        )
        assert signal is not None
        assert signal.severity == "CRITICAL"

    def test_a_critical_gap_that_is_improving_does_not_fire(self):
        assert detect_critical_concentration(
            "W1", "Chennai", "WATER", gap_score=88.0, gap_severity="CRITICAL",
            previous=40, recent=20,
        ) is None

    def test_a_non_critical_gap_does_not_fire(self):
        assert detect_critical_concentration(
            "W1", "Chennai", "WATER", gap_score=60.0, gap_severity="HIGH",
            previous=20, recent=40,
        ) is None

    def test_band_vocabulary_is_not_confused_with_risk_severity(self):
        # Gaps band as MODERATE where risks are MEDIUM; the rule keys on the
        # gap's own band name.
        assert detect_critical_concentration(
            "W1", "Chennai", "WATER", gap_score=40.0, gap_severity="MODERATE",
            previous=20, recent=40,
        ) is None


class TestDetectRisks:
    def test_a_single_window_is_not_judged(self):
        # No direction to infer from one observation.
        assert detect_risks(demand_rows=[demand(count=100)], gap_snapshots=[], projects=[]) == []

    def test_a_spike_is_detected_from_two_windows(self):
        rows = [demand(count=10, end_days_ago=30), demand(count=90)]
        signals = detect_risks(demand_rows=rows, gap_snapshots=[], projects=[])
        assert [s.risk_type for s in signals] == ["SPIKE"]

    def test_insertion_order_does_not_change_which_window_is_recent(self):
        # Both orders describe the same history: 10 in May, 90 in June. Detecting
        # a fall instead would mean "previous" and "recent" were swapped.
        for rows in (
            [demand(count=10, end_days_ago=30), demand(count=90)],
            [demand(count=90), demand(count=10, end_days_ago=30)],
        ):
            signals = detect_risks(demand_rows=rows, gap_snapshots=[], projects=[])
            assert [s.risk_type for s in signals] == ["SPIKE"]
            assert signals[0].evidence["previous_count"] == 10
            assert signals[0].evidence["recent_count"] == 90

    def test_a_gap_with_only_one_window_behind_it_is_skipped(self):
        # Regression guard: the gap rules index the previous window and must not
        # assume it exists.
        rows = [demand(count=40)]
        assert detect_risks(demand_rows=rows, gap_snapshots=[gap()], projects=[]) == []

    def test_sector_filter_limits_the_output(self):
        rows = [
            demand(ward_code="W1", sector="WATER", count=10, end_days_ago=30),
            demand(ward_code="W1", sector="WATER", count=90),
            demand(ward_code="W2", sector="ROAD", count=10, end_days_ago=30),
            demand(ward_code="W2", sector="ROAD", count=90),
        ]
        signals = detect_risks(demand_rows=rows, gap_snapshots=[], projects=[],
                               sectors=["WATER"])
        assert {s.sector for s in signals} == {"WATER"}

    def test_a_stalled_project_is_detected_without_any_demand_history(self):
        signals = detect_risks(
            demand_rows=[], gap_snapshots=[], projects=[project()],
            project_elapsed={"P1": 0.7},
        )
        assert [s.risk_type for s in signals] == ["STALLED_ABSORPTION"]

    def test_results_are_ordered_most_severe_first(self):
        rows = [demand(count=10, end_days_ago=30), demand(count=90)]
        signals = detect_risks(demand_rows=rows, gap_snapshots=[gap()], projects=[])
        severities = [s.severity for s in signals]
        assert severities == sorted(severities, key=lambda s: ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(s))

    def test_no_signals_gives_an_empty_result(self):
        assert detect_risks(demand_rows=[], gap_snapshots=[], projects=[]) == []


class TestSummaries:
    def test_counts_always_report_every_type_and_severity(self):
        summary = summarise_risks([])
        assert summary["by_type"]["SPIKE"] == 0
        assert summary["by_severity"]["CRITICAL"] == 0

    def test_counts_reflect_the_signals(self):
        rows = [demand(count=10, end_days_ago=30), demand(count=90)]
        summary = summarise_risks(detect_risks(demand_rows=rows, gap_snapshots=[], projects=[]))
        assert summary["by_type"]["SPIKE"] == 1


class TestConfirmatorySignals:
    def test_two_rules_firing_on_one_ward_sector_confirm_each_other(self):
        rows = [demand(count=20, end_days_ago=30), demand(count=120)]
        signals = detect_risks(
            demand_rows=rows,
            gap_snapshots=[gap()],
            projects=[project(budget=800.0, spent=50.0)],
        )
        assert len(confirmatory_signals(signals)) == len(signals)

    def test_a_lone_rule_is_not_confirmatory(self):
        rows = [demand(count=10, end_days_ago=30), demand(count=90)]
        signals = detect_risks(demand_rows=rows, gap_snapshots=[], projects=[])
        assert confirmatory_signals(signals) == []