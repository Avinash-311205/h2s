"""Tests for the pure helpers everything else depends on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.utils import (
    clamp,
    group_by,
    linear_slope,
    mean,
    naive_utc,
    percent_change,
    percentile,
    safe_div,
    stdev,
    window_start,
    z_score,
)


class TestSafeDiv:
    def test_divides_normally(self):
        assert safe_div(10, 4) == 2.5

    def test_zero_denominator_returns_default_instead_of_raising(self):
        # A ward with no population must not take down a whole analysis run.
        assert safe_div(10, 0) == 0.0
        assert safe_div(10, 0, default=1.0) == 1.0


class TestPercentChange:
    def test_normal_increase(self):
        assert percent_change(100, 150) == 50.0

    def test_normal_decrease(self):
        assert percent_change(100, 50) == -50.0

    def test_zero_baseline_is_capped_not_infinite(self):
        # From zero to anything is unbounded; capping keeps one new category from
        # dominating every ranking it appears in.
        assert percent_change(0, 40) == 100.0

    def test_zero_to_zero_is_no_change(self):
        assert percent_change(0, 0) == 0.0


class TestLinearSlope:
    def test_rising_series_has_positive_slope(self):
        assert linear_slope([1.0, 2.0, 3.0]) > 0

    def test_falling_series_has_negative_slope(self):
        assert linear_slope([3.0, 2.0, 1.0]) < 0

    def test_flat_series_has_zero_slope(self):
        assert linear_slope([5.0, 5.0, 5.0]) == 0.0

    def test_single_point_has_no_slope(self):
        assert linear_slope([7.0]) == 0.0

    def test_two_points_use_the_difference(self):
        assert linear_slope([2.0, 6.0]) == 4.0


class TestStatistics:
    def test_mean(self):
        assert mean([2.0, 4.0, 6.0]) == 4.0

    def test_stdev_of_constant_series_is_zero(self):
        assert stdev([3.0, 3.0, 3.0]) == 0.0

    def test_percentile_picks_the_median(self):
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5

    def test_percentile_of_empty_sequence_is_zero(self):
        assert percentile([], 0.9) == 0.0

    def test_z_score_against_the_mean(self):
        assert z_score(5.0, [1.0, 2.0, 3.0, 4.0, 5.0]) > 0

    def test_z_score_of_an_identical_value_is_zero(self):
        assert z_score(3.0, [3.0, 3.0, 3.0]) == 0.0

    def test_z_score_without_spread_does_not_divide_by_zero(self):
        # Every ward reporting exactly the same intensity is unremarkable, not
        # infinitely remarkable.
        assert z_score(4.0, [4.0, 4.0, 4.0]) == 0.0


class TestClamp:
    def test_clamps_into_range(self):
        assert clamp(5.0, 0.0, 1.0) == 1.0
        assert clamp(-2.0, 0.0, 1.0) == 0.0
        assert clamp(0.4, 0.0, 1.0) == 0.4


class TestGroupBy:
    def test_groups_preserving_order(self):
        rows = ["aa", "ab", "ba", "bb"]
        grouped = group_by(rows, lambda value: value[0])
        assert grouped == {"a": ["aa", "ab"], "b": ["ba", "bb"]}


class TestNaiveUtc:
    def test_aware_values_are_converted_to_naive_utc(self):
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert naive_utc(aware) == datetime(2026, 1, 1, 12, 0)
        assert naive_utc(aware).tzinfo is None

    def test_naive_values_pass_through_unchanged(self):
        naive = datetime(2026, 1, 1, 12, 0)
        assert naive_utc(naive) == naive

    def test_non_utc_offset_is_shifted_before_the_tzinfo_is_dropped(self):
        # 12:00+05:30 is 06:30 UTC. Dropping the tzinfo without converting would
        # silently move the instant by five and a half hours.
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        assert naive_utc(aware) == datetime(2026, 1, 1, 6, 30)

    def test_round_trip_is_stable(self):
        # This is what keeps de-duplication keys comparable with SQLite's output.
        assert naive_utc(naive_utc(datetime(2026, 5, 1))) == datetime(2026, 5, 1)


class TestWindowStart:
    def test_window_starts_the_right_number_of_days_back(self):
        reference = datetime(2026, 3, 31, 0, 0, 0)
        assert window_start(30, reference=reference) == datetime(2026, 3, 1, 0, 0, 0)