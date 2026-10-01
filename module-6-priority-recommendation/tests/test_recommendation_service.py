"""Recommendation wording and the indicative cost model."""

from __future__ import annotations

import pytest

from app.services.priority_service import score_hotspot
from app.services.recommendation_service import build_action_plan, build_cost_lakhs, build_recommendation
from tests.test_priority_service import make_hotspot


def scored(**overrides) -> dict:
    return score_hotspot(make_hotspot(**overrides))


class TestRecommendationContent:
    def test_recommendation_is_a_sentence(self):
        text = build_recommendation(scored())
        assert text.endswith(".")
        assert len(text) > 20

    def test_coverage_gap_drives_an_expansion_recommendation(self):
        text = build_recommendation(scored(gap_score=70.0))
        assert "coverage" in text.lower()

    def test_stalled_budget_drives_an_escalation_recommendation(self):
        text = build_recommendation(scored(gap_score=5.0, stalled_share=0.5))
        assert "escalate" in text.lower() or "stalled" in text.lower()

    def test_rising_trend_drives_an_intervention_recommendation(self):
        text = build_recommendation(
            scored(gap_score=0.0, stalled_share=0.0, unspent_share=0.0, delay_days=0.0)
        )
        assert "rising" in text.lower() or "intervene" in text.lower()

    def test_insufficient_data_says_so(self):
        """With nothing measurable the output must not invent a finding."""
        text = build_recommendation(
            scored(
                gap_score=None,
                stalled_share=None,
                unspent_share=None,
                delay_days=None,
                trend_direction="UNKNOWN",
                demand_rate_per_1000=None,
                population=0,
            )
        )
        assert "not sufficient" in text.lower() or "not measured" in text.lower()


class TestRecommendationHonesty:
    def test_unmeasured_factors_are_named_in_the_text(self):
        text = build_recommendation(scored(gap_score=None))
        assert "not measured" in text.lower()

    def test_unspent_budget_is_not_reported_as_needed_money(self):
        """Money already sanctioned and unspent is a delivery problem."""
        text = build_recommendation(
            scored(gap_score=5.0, stalled_share=0.0, unspent_share=0.6)
        )
        assert "expedite" in text.lower() or "unspent" in text.lower()


class TestCostModel:
    def test_cost_grows_with_ward_count(self):
        one = build_cost_lakhs(scored(ward_codes=["W1"]))
        three = build_cost_lakhs(scored(ward_codes=["W1", "W2", "W3"]))
        assert three > one

    def test_cost_is_positive(self):
        assert build_cost_lakhs(scored()) > 0

    def test_critical_complaints_add_to_the_envelope(self):
        without = build_cost_lakhs(scored(critical_complaints=0))
        with_critical = build_cost_lakhs(scored(critical_complaints=10))
        assert with_critical > without


class TestActionPlan:
    def _plan(self):
        rows = []
        for code, score in (("A", 80.0), ("B", 60.0), ("C", 40.0)):
            row = scored()
            row.update({"hotspot_code": code, "score": score, "rank": 0})
            row["recommended_cost_lakhs"] = build_cost_lakhs(row)
            rows.append(row)
        rows.sort(key=lambda r: -r["score"])
        for i, row in enumerate(rows, start=1):
            row["rank"] = i
        return build_action_plan(rows)

    def test_plan_is_in_rank_order(self):
        assert [i["hotspot_code"] for i in self._plan()] == ["A", "B", "C"]

    def test_cumulative_cost_accumulates(self):
        plan = self._plan()
        running = 0.0
        for item in plan:
            running += item["recommended_cost_lakhs"]
            assert item["cumulative_cost_lakhs"] == pytest.approx(running, abs=0.05)

    def test_every_plan_item_carries_a_recommendation(self):
        assert all(i["recommendation"] for i in self._plan())

    def test_empty_input_gives_empty_plan(self):
        assert build_action_plan([]) == []
