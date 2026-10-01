"""Persistence: score replacement, history, and run lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.enums import RunStatus
from app.models.priority_tables import PriorityScore
from app.repositories.priority_repository import PriorityRepository


def make_row(code: str = "HS-1", score: float = 70.0, rank: int = 1) -> dict:
    return {
        "hotspot_code": code,
        "district": "Chennai",
        "rank": rank,
        "centroid_latitude": 13.08,
        "centroid_longitude": 80.27,
        "score": score,
        "band": "HIGH",
        "factors": {"demand": {"component": 0.8, "measured": True}},
        "evidence": {"complaints_per_1000": 20.0},
        "population": 10_000,
        "total_complaints": 200,
        "critical_complaints": 10,
        "ward_codes": ["W1"],
        "sectors": ["WATER"],
        "dominant_sector": "WATER",
        "recommendation": "Do the thing.",
        "recommended_cost_lakhs": 75.0,
        "unmeasured_factors": [],
        "window_days": 30,
    }


class TestReplaceScores:
    def test_scores_are_persisted(self, session: Session):
        repo = PriorityRepository(session)
        run = repo.start_run()
        repo.replace_scores([make_row()], run_id=run.id, source="sqlite:///x.db")
        assert repo.count_scores() == 1
        assert repo.get_score("HS-1").score == 70.0

    def test_second_run_replaces_rather_than_accumulates(self, session: Session):
        repo = PriorityRepository(session)
        first = repo.start_run()
        repo.replace_scores([make_row("A"), make_row("B", rank=2)], run_id=first.id, source="s")
        second = repo.start_run()
        repo.replace_scores([make_row("A")], run_id=second.id, source="s")

        assert repo.count_scores() == 1
        rows = repo.scored_rows()
        assert [r.hotspot_code for r in rows] == ["A"]

    def test_replacing_scores_archives_the_previous_ranking(self, session: Session):
        repo = PriorityRepository(session)
        first = repo.start_run()
        repo.replace_scores([make_row("A", 80.0)], run_id=first.id, source="s")
        second = repo.start_run()
        repo.replace_scores([make_row("A", 55.0)], run_id=second.id, source="s")

        history = repo.history_for("A")
        assert len(history) == 1
        assert history[0].score == 80.0

    def test_source_is_recorded_on_each_score(self, session: Session):
        repo = PriorityRepository(session)
        run = repo.start_run()
        repo.replace_scores([make_row()], run_id=run.id, source="sqlite:///upstream.db")
        assert repo.get_score("HS-1").intelligence_source == "sqlite:///upstream.db"


class TestMovement:
    def test_no_previous_run_reports_no_movement(self, session: Session):
        repo = PriorityRepository(session)
        run = repo.start_run()
        repo.replace_scores([make_row()], run_id=run.id, source="s")
        movement = repo.movement_for("HS-1")
        assert movement["previous_score"] is None
        assert movement["score_change"] is None

    def test_score_change_against_the_previous_run(self, session: Session):
        repo = PriorityRepository(session)
        first = repo.start_run()
        repo.replace_scores([make_row("A", 80.0)], run_id=first.id, source="s")
        second = repo.start_run()
        repo.replace_scores([make_row("A", 65.0)], run_id=second.id, source="s")

        movement = repo.movement_for("A")
        assert movement["previous_score"] == 80.0
        assert movement["score_change"] == pytest.approx(-15.0)

    def test_rank_change_is_reported_separately_from_score(self, session: Session):
        """Score holding steady while relative standing shifts is still a change."""
        repo = PriorityRepository(session)
        first = repo.start_run()
        repo.replace_scores([make_row("A", 50.0, rank=1), make_row("B", 40.0, rank=2)], run_id=first.id, source="s")
        second = repo.start_run()
        repo.replace_scores([make_row("A", 50.0, rank=2), make_row("B", 40.0, rank=1)], run_id=second.id, source="s")

        movement = repo.movement_for("A")
        assert movement["score_change"] == pytest.approx(0.0)
        assert movement["rank_change"] == -1


class TestRunLifecycle:
    def test_run_starts_as_running(self, session: Session):
        repo = PriorityRepository(session)
        run = repo.start_run()
        assert run.status == RunStatus.RUNNING.value
        assert run.finished_at is None

    def test_successful_run_records_counts_and_duration(self, session: Session):
        repo = PriorityRepository(session)
        run = repo.start_run()
        repo.finish_run(run, RunStatus.SUCCESS.value, hotspots_scored=3)
        assert run.status == RunStatus.SUCCESS.value
        assert run.hotspots_scored == 3
        assert run.finished_at is not None
        assert run.duration_ms >= 0

    def test_failed_run_records_its_error(self, session: Session):
        repo = PriorityRepository(session)
        run = repo.start_run()
        repo.finish_run(run, RunStatus.FAILED.value, error="upstream missing")
        assert run.status == RunStatus.FAILED.value
        assert run.error == "upstream missing"

    def test_latest_successful_run_ignores_failures(self, session: Session):
        repo = PriorityRepository(session)
        ok = repo.start_run()
        repo.finish_run(ok, RunStatus.SUCCESS.value, hotspots_scored=2)
        bad = repo.start_run()
        repo.finish_run(bad, RunStatus.FAILED.value, error="boom")

        assert repo.latest_successful_run().id == ok.id

    def test_recent_runs_are_newest_first(self, session: Session):
        repo = PriorityRepository(session)
        for _ in range(3):
            repo.start_run()
        runs = repo.recent_runs()
        assert [r.id for r in runs] == sorted([r.id for r in runs], reverse=True)