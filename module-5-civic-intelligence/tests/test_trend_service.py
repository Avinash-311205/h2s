"""Tests for trend detection.

The central rule being protected: a trend needs two observations, and a change
smaller than the threshold is not a trend. Both exist to stop a dashboard from
reporting noise as a finding.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from app.services.trend_service import (
    TrendResult,
    average_slope,
    build_series,
    classify_direction,
    compute_all_trends,
    compute_trend,
    emerging_sectors,
    momentum_of,
    sector_trend_matrix,
    summarise_directions,
)


def demand_row(ward_code, sector, count, *, end_days_ago=0, window_days=30):
    from app.models.intelligence_tables import DemandWindow

    end = datetime(2026, 6, 30) - timedelta(days=end_days_ago)
    return DemandWindow(
        ward_code=ward_code,
        sector=sector,
        window_days=window_days,
        window_start=end - timedelta(days=window_days),
        window_end=end,
        complaint_count=count,
        critical_count=0,
        avg_severity=3.0,
        population=10_000,
    )


class TestClassifyDirection:
    def test_sustained_rise_is_worsening(self):
        assert classify_direction(50.0, 2.0) == "WORSENING"

    def test_sustained_fall_is_improving(self):
        assert classify_direction(-50.0, -2.0) == "IMPROVING"

    def test_a_large_rise_with_a_flat_slope_is_not_a_trend(self):
        # One bad window: the level moved but the series did not.
        assert classify_direction(80.0, 0.0) == "STABLE"

    def test_a_small_move_is_below_threshold(self):
        assert classify_direction(3.0, 0.1) == "STABLE"

    def test_threshold_is_configurable(self):
        assert classify_direction(8.0, 1.0, threshold_pct=5.0) == "WORSENING"


class TestComputeTrend:
    def test_a_single_observation_has_no_direction(self):
        result = compute_trend(ward_code="W1", sector="WATER", series=[42])
        assert result.direction == "UNKNOWN"
        assert result.sample_count == 1

    def test_no_observations_is_unknown_and_safe(self):
        result = compute_trend(ward_code="W1", sector="WATER", series=[])
        assert result.direction == "UNKNOWN"
        assert result.first_count == 0

    def test_rising_series_is_worsening(self):
        result = compute_trend(ward_code="W1", sector="WATER", series=[10, 20, 40])
        assert result.direction == "WORSENING"
        assert result.first_count == 10
        assert result.last_count == 40

    def test_falling_series_is_improving(self):
        assert compute_trend(ward_code="W1", sector="WATER", series=[40, 20, 10]).direction == "IMPROVING"

    def test_flat_series_is_stable(self):
        assert compute_trend(ward_code="W1", sector="WATER", series=[30, 30, 30]).direction == "STABLE"

    def test_pct_change_is_first_to_last(self):
        result = compute_trend(ward_code="W1", sector="WATER", series=[50, 60, 100])
        assert result.pct_change == pytest.approx(100.0)

    def test_series_is_preserved_in_order(self):
        result = compute_trend(ward_code="W1", sector="WATER", series=[1, 2, 3])
        assert result.series == [1, 2, 3]

    def test_window_is_carried_through(self):
        result = compute_trend(ward_code="W1", sector="WATER", series=[1, 2], window_days=90)
        assert result.window_days == 90


class TestMomentum:
    def test_momentum_is_the_most_recent_step(self):
        assert momentum_of([10, 40, 90]) == 50

    def test_a_recent_rebound_shows_as_positive_momentum(self):
        # Improving on average but worsening this window: exactly the case that a
        # single average would hide.
        assert momentum_of([100, 80, 95]) > 0

    def test_a_single_point_has_no_momentum(self):
        assert momentum_of([7]) == 0.0


class TestBuildSeries:
    def test_rows_are_ordered_chronologically_not_by_insertion(self):
        rows = [demand_row("W1", "WATER", 30, end_days_ago=0), demand_row("W1", "WATER", 10, end_days_ago=30)]
        assert build_series(rows, 30) == [10, 30]

    def test_other_window_lengths_are_excluded(self):
        rows = [demand_row("W1", "WATER", 10, window_days=30), demand_row("W1", "WATER", 99, window_days=90)]
        assert build_series(rows, 30) == [10]

    def test_no_matching_window_yields_an_empty_series(self):
        assert build_series([demand_row("W1", "WATER", 10, window_days=90)], 30) == []


class TestComputeAllTrends:
    def test_each_ward_sector_gets_a_trend_per_window(self):
        rows = [
            demand_row("W1", "WATER", 10, end_days_ago=30),
            demand_row("W1", "WATER", 20),
            demand_row("W2", "ROAD", 5, end_days_ago=30),
            demand_row("W2", "ROAD", 6),
        ]
        results = compute_all_trends(rows, windows=[30])
        assert len(results) == 2
        assert {r.ward_code for r in results} == {"W1", "W2"}

    def test_sector_filter_restricts_the_run(self):
        rows = [demand_row("W1", "WATER", 10), demand_row("W2", "ROAD", 10)]
        results = compute_all_trends(rows, windows=[30], sectors=["WATER"])
        assert [r.sector for r in results] == ["WATER"]

    def test_worsening_sorts_before_stable_and_improving(self):
        rows = [
            demand_row("W1", "WATER", 10, end_days_ago=30), demand_row("W1", "WATER", 40),
            demand_row("W2", "WATER", 20, end_days_ago=30), demand_row("W2", "WATER", 21),
            demand_row("W3", "WATER", 40, end_days_ago=30), demand_row("W3", "WATER", 5),
        ]
        directions = [r.direction for r in compute_all_trends(rows, windows=[30])]
        assert directions[0] == "WORSENING"
        assert directions[-1] == "IMPROVING"

    def test_wards_with_no_data_produce_nothing(self):
        assert compute_all_trends([], windows=[30]) == []

    def test_both_configured_windows_are_computed_by_default(self):
        rows = [
            demand_row("W1", "WATER", 10, end_days_ago=90, window_days=90),
            demand_row("W1", "WATER", 20, window_days=90),
        ]
        results = compute_all_trends(rows)
        assert {r.window_days for r in results} == {90}


class TestSummaries:
    def test_direction_counts_always_report_every_direction(self):
        counts = summarise_directions([])
        assert set(counts) == {"WORSENING", "IMPROVING", "STABLE", "UNKNOWN"}

    def test_sector_matrix_counts_by_direction(self):
        trends = [
            TrendResult("W1", "WATER", 30, "WORSENING", 1, 2, 2, 100.0, 1.0, 1.0, 0.0),
            TrendResult("W2", "WATER", 30, "WORSENING", 1, 2, 2, 90.0, 1.0, 1.0, 0.0),
            TrendResult("W3", "ROAD", 30, "IMPROVING", 2, 1, 2, -50.0, -1.0, -1.0, 0.0),
        ]
        matrix = sector_trend_matrix(trends)
        assert matrix["WATER"]["WORSENING"] == 2
        assert matrix["ROAD"]["IMPROVING"] == 1

    def test_emerging_sectors_returns_only_worsening(self):
        trends = [
            TrendResult("W1", "WATER", 30, "WORSENING", 1, 9, 2, 800.0, 8.0, 8.0, 0.0),
            TrendResult("W2", "ROAD", 30, "IMPROVING", 9, 1, 2, -88.0, -8.0, -8.0, 0.0),
        ]
        assert [t.ward_code for t in emerging_sectors(trends)] == ["W1"]

    def test_emerging_sectors_ranks_by_recent_momentum(self):
        trends = [
            TrendResult("W1", "WATER", 30, "WORSENING", 1, 10, 2, 900.0, 9.0, 5.0, 0.0),
            TrendResult("W2", "WATER", 30, "WORSENING", 1, 12, 2, 1100.0, 11.0, 11.0, 0.0),
        ]
        assert [t.ward_code for t in emerging_sectors(trends)] == ["W2", "W1"]

    def test_average_slope_of_nothing_is_zero(self):
        assert average_slope([]) == 0.0