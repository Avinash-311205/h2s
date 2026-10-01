"""Scoring behaviour: the arithmetic and, more importantly, the honesty rules."""

from __future__ import annotations

import pytest

from app.core.enums import PriorityBand, ScoreFactor, TrendDirection
from app.services.intelligence_client import HotspotSignals
from app.services.priority_service import band_for, rank_scores, score_hotspot


def make_hotspot(**overrides) -> HotspotSignals:
    """A fully measured hotspot; override one field to test a missing signal."""
    base = dict(
        hotspot_code="HS-1",
        district="Chennai",
        centroid_latitude=13.08,
        centroid_longitude=80.27,
        ward_codes=["W1"],
        sectors=["WATER"],
        population=10_000,
        total_complaints=200,
        critical_complaints=10,
        window_days=30,
        demand_rate_per_1000=20.0,
        avg_severity=4.0,
        pct_growth=50.0,
        trend_direction=TrendDirection.WORSENING,
        gap_score=50.0,
        stalled_share=0.25,
        unspent_share=0.30,
        delay_days=45.0,
        dominant_sector="WATER",
    )
    base.update(overrides)
    return HotspotSignals(**base)


class TestBands:
    def test_high_band_at_threshold(self):
        assert band_for(70.0) is PriorityBand.HIGH
        assert band_for(100.0) is PriorityBand.HIGH

    def test_medium_band(self):
        assert band_for(69.9) is PriorityBand.MEDIUM
        assert band_for(45.0) is PriorityBand.MEDIUM

    def test_low_band(self):
        assert band_for(44.9) is PriorityBand.LOW
        assert band_for(0.0) is PriorityBand.LOW


class TestScoreRange:
    def test_score_is_within_zero_to_hundred(self):
        result = score_hotspot(make_hotspot())
        assert 0.0 <= result["score"] <= 100.0

    def test_a_worse_hotspot_scores_higher(self):
        mild = score_hotspot(make_hotspot(demand_rate_per_1000=3.0, gap_score=5.0))
        severe = score_hotspot(make_hotspot(demand_rate_per_1000=40.0, gap_score=90.0))
        assert severe["score"] > mild["score"]

    def test_saturation_caps_each_factor_at_full_marks(self):
        """Absurd inputs must saturate each factor, not push the composite past 100.

        The composite cannot reach 100 here because the trend factor is still
        only rising by 50%, so the bound to assert is that no individual factor
        exceeds full marks and the total stays in range.
        """
        result = score_hotspot(
            make_hotspot(
                demand_rate_per_1000=10_000.0,
                gap_score=100_000.0,
                stalled_share=99.0,
                delay_days=100_000.0,
            )
        )
        assert all(f["component"] <= 1.0 for f in result["factors"].values())
        assert result["score"] <= 100.0
        assert result["factors"]["demand"]["component"] == 1.0
        assert result["factors"]["coverage"]["component"] == 1.0

    def test_fully_measured_hotspot_can_reach_one_hundred(self):
        result = score_hotspot(
            make_hotspot(
                demand_rate_per_1000=10_000.0,
                gap_score=100_000.0,
                stalled_share=99.0,
                unspent_share=99.0,
                delay_days=100_000.0,
                avg_severity=5.0,
                trend_direction=TrendDirection.WORSENING,
                pct_growth=10_000.0,
            )
        )
        assert result["evidence_coverage"] == 1.0
        assert result["score"] == 100.0


class TestUnmeasuredIsNotZero:
    """The central rule: absence of data is not a low score."""

    def test_missing_demand_is_flagged_unmeasured(self):
        result = score_hotspot(make_hotspot(population=0, demand_rate_per_1000=None))
        assert ScoreFactor.DEMAND in result["unmeasured_factors"]
        assert result["factors"]["demand"]["measured"] is False

    def test_missing_demand_scores_lower_than_measured_zero(self):
        """An unmeasured factor must not be as good as a genuinely measured zero."""
        unmeasured = score_hotspot(make_hotspot(demand_rate_per_1000=None))
        measured_zero = score_hotspot(make_hotspot(demand_rate_per_1000=0.0))
        assert unmeasured["score"] < measured_zero["score"]

    def test_missing_trend_is_flagged(self):
        result = score_hotspot(make_hotspot(trend_direction=TrendDirection.UNKNOWN))
        assert ScoreFactor.TREND in result["unmeasured_factors"]

    def test_missing_coverage_is_flagged(self):
        result = score_hotspot(make_hotspot(gap_score=None))
        assert ScoreFactor.COVERAGE in result["unmeasured_factors"]

    def test_missing_delivery_data_is_flagged(self):
        result = score_hotspot(make_hotspot(stalled_share=None, unspent_share=None, delay_days=None))
        assert ScoreFactor.SERVICE_FAILURE in result["unmeasured_factors"]

    def test_all_factors_measured_reports_nothing_unmeasured(self):
        result = score_hotspot(make_hotspot())
        assert result["unmeasured_factors"] == []

    def test_partial_coverage_does_not_inflate_score(self):
        """A hotspot measured on one factor must not outscore a fully measured one."""
        partial = score_hotspot(
            make_hotspot(
                gap_score=None,
                stalled_share=None,
                unspent_share=None,
                delay_days=None,
                trend_direction=TrendDirection.UNKNOWN,
            )
        )
        full = score_hotspot(make_hotspot())
        assert partial["score"] < full["score"]

    def test_unmeasured_factors_are_deduplicated(self):
        result = score_hotspot(
            make_hotspot(gap_score=None, trend_direction=TrendDirection.UNKNOWN),
        )
        assert len(result["unmeasured_factors"]) == len(set(result["unmeasured_factors"]))


class TestTrendWeighting:
    def test_rising_scores_above_stable(self):
        rising = score_hotspot(make_hotspot(trend_direction=TrendDirection.WORSENING, pct_growth=50.0))
        stable = score_hotspot(make_hotspot(trend_direction=TrendDirection.STABLE, pct_growth=0.0))
        assert rising["score"] > stable["score"]

    def test_falling_scores_lowest(self):
        falling = score_hotspot(
            make_hotspot(trend_direction=TrendDirection.IMPROVING, pct_growth=-50.0)
        )
        stable = score_hotspot(make_hotspot(trend_direction=TrendDirection.STABLE, pct_growth=0.0))
        assert falling["score"] < stable["score"]

    def test_rising_without_a_measured_growth_rate_is_still_measured(self):
        """Direction is evidence in its own right."""
        result = score_hotspot(
            make_hotspot(trend_direction=TrendDirection.WORSENING, pct_growth=None)
        )
        assert result["factors"]["trend"]["measured"] is True


class TestDemandNormalisation:
    def test_same_complaints_in_a_smaller_ward_scores_higher(self):
        """Rate, not volume: population is the point of the normalisation.

        Rates are chosen inside the calibrated reference range - two values above
        it would both saturate at full marks and the test would pass for the
        wrong reason.
        """
        large = score_hotspot(make_hotspot(population=100_000, demand_rate_per_1000=1.0))
        small = score_hotspot(make_hotspot(population=2_000, demand_rate_per_1000=4.0))
        assert small["score"] > large["score"]

    def test_demand_component_is_proportional_below_the_reference(self):
        below = score_hotspot(make_hotspot(demand_rate_per_1000=2.5))
        assert below["factors"]["demand"]["component"] == pytest.approx(0.5, abs=0.01)

    def test_demand_saturates_at_the_reference(self):
        above = score_hotspot(make_hotspot(demand_rate_per_1000=100.0))
        assert above["factors"]["demand"]["component"] == 1.0


class TestRanking:
    def test_ranks_are_dense_and_ordered(self):
        rows = [
            {"hotspot_code": "B", "score": 50.0},
            {"hotspot_code": "A", "score": 80.0},
            {"hotspot_code": "C", "score": 20.0},
        ]
        ranked = rank_scores(rows)
        assert [r["hotspot_code"] for r in ranked] == ["A", "B", "C"]
        assert [r["rank"] for r in ranked] == [1, 2, 3]

    def test_ties_break_deterministically(self):
        """Stable ordering stops the rank column churning between runs."""
        rows = [
            {"hotspot_code": "ZZZ", "score": 50.0},
            {"hotspot_code": "AAA", "score": 50.0},
        ]
        first = [r["hotspot_code"] for r in rank_scores(list(rows))]
        second = [r["hotspot_code"] for r in rank_scores(list(reversed(rows)))]
        assert first == second == ["AAA", "ZZZ"]


class TestEvidence:
    def test_evidence_carries_the_underlying_numbers(self):
        result = score_hotspot(make_hotspot())
        evidence = result["evidence"]
        assert evidence["complaints_per_1000"] == 20.0
        assert evidence["avg_severity"] == 4.0
        assert evidence["gap_score"] == 50.0

    def test_dominant_sector_is_carried_through(self):
        assert score_hotspot(make_hotspot(dominant_sector="ROAD"))["dominant_sector"] == "ROAD"