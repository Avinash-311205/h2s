"""Tests for scoring.py -- the transparent priority-scoring logic."""

import pytest

from scoring import (
    compute_priority_score,
    density_for_region,
    generate_recommendation,
    normalise_count,
    normalise_severity,
    normalise_days_open,
    W_VOLUME,
    W_SEVERITY,
    W_DENSITY,
    W_DAYS_OPEN,
)


class TestNormalisation:
    def test_count_capped_at_reference_max(self):
        assert normalise_count(0) == 0.0
        assert normalise_count(100) == 100.0
        assert normalise_count(500) == 100.0  # above cap clamps to 100
        assert normalise_count(50) == 50.0

    def test_severity_scale(self):
        assert normalise_severity(5.0) == 100.0
        assert normalise_severity(2.5) == 50.0
        assert normalise_severity(10.0) == 100.0  # out-of-range clamps

    def test_days_open_capped(self):
        assert normalise_days_open(90) == 100.0
        assert normalise_days_open(45) == 50.0
        assert normalise_days_open(10000) == 100.0

    def test_unknown_region_has_fallback_density(self):
        assert 0 <= density_for_region("nowhere_ville") <= 30000


class TestWeights:
    def test_weights_sum_to_one(self):
        total = W_VOLUME + W_SEVERITY + W_DENSITY + W_DAYS_OPEN
        assert pytest.approx(total, abs=1e-9) == 1.0


class TestPriorityScore:
    def test_always_within_0_100(self):
        score, factors = compute_priority_score(10000, 10.0, "downtown", 100000.0)
        assert 0.0 <= score <= 100.0

    def test_worst_case_scores_near_100(self):
        # Hand-checked ceiling: volume 35 + severity 30 + days 20 = 85, plus
        # density for downtown (25000/30000 -> 83.33 x 0.15 = 12.5) = 97.5.
        score, _ = compute_priority_score(100, 5.0, "downtown", 90)
        assert pytest.approx(score, abs=0.01) == 97.5

    def test_all_factors_present_in_breakdown(self):
        _, factors = compute_priority_score(20, 3.0, "old_city", 30)
        assert set(factors.keys()) == {
            "volume",
            "severity",
            "population_density",
            "days_open",
        }
        for f in factors.values():
            assert f["contribution"] == pytest.approx(
                f["normalised"] * f["weight"], abs=0.01
            )

    def test_dense_region_outranks_sparse_region(self):
        dense, _ = compute_priority_score(10, 3.0, "downtown", 10)
        sparse, _ = compute_priority_score(10, 3.0, "suburb_north", 10)
        assert dense > sparse


class TestRecommendationTemplate:
    def test_high_score_mentions_urgent(self):
        text = generate_recommendation(
            "roads", 50, 4.8, "downtown", 60, priority_score=82
        )
        assert "roads" in text
        assert "urgent" in text

    def test_low_score_mentions_budget_allocation(self):
        text = generate_recommendation(
            "roads", 5, 2.0, "suburb_south", 5, priority_score=20
        )
        assert "budget allocation" in text
        assert "urgent" not in text