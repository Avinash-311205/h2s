"""End-to-end orchestration: snapshot in, ranking and plan out."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.priority_tables import PriorityRun
from app.services import analytics_service
from app.services.analytics_service import NothingScoredError
from app.services.intelligence_client import IntelligenceClient
import synthetic_snapshot as factory
from tests.conftest import use_intelligence_db


@pytest.fixture
def populated(tmp_path: Path, monkeypatch) -> str:
    """A healthy Module 5 snapshot, returned as its URL."""
    path = tmp_path / "populated.db"
    conn = factory.healthy_snapshot(path)
    conn.close()
    url = f"sqlite:///{path}"
    use_intelligence_db(monkeypatch, url)
    return url


class TestRecompute:
    def test_scores_every_hotspot(self, session: Session, populated):
        result = analytics_service.recompute(session)
        assert result["scored"] == 2
        assert result["degraded_factors"] == []

    def test_result_reports_source_and_run_id(self, session: Session, populated):
        result = analytics_service.recompute(session)
        assert result["source"] == populated
        assert result["run_id"] > 0

    def test_recompute_is_idempotent(self, session: Session, populated):
        first = analytics_service.recompute(session)
        second = analytics_service.recompute(session)
        assert first["scored"] == second["scored"] == 2
        assert session.query(PriorityRun).count() == 2

    def test_run_is_marked_successful(self, session: Session, populated):
        result = analytics_service.recompute(session)
        runs = analytics_service.runs_payload(session)["runs"]
        assert runs[0]["status"] == "SUCCESS"
        assert runs[0]["id"] == result["run_id"]


class TestPayloads:
    def test_priorities_are_ordered_by_rank(self, session: Session, populated):
        analytics_service.recompute(session)
        items = analytics_service.priorities_payload(session)["priorities"]
        assert [i["rank"] for i in items] == sorted(i["rank"] for i in items)
        assert items[0]["score"] >= items[-1]["score"]

    def test_dominant_sector_is_populated_for_real_data(self, session: Session, populated):
        analytics_service.recompute(session)
        items = analytics_service.priorities_payload(session)["priorities"]
        assert all(i["dominant_sector"] for i in items)

    def test_summary_counts_bands_to_the_total(self, session: Session, populated):
        analytics_service.recompute(session)
        summary = analytics_service.summary_payload(session)
        assert sum(summary["bands"].values()) == summary["scored"] == 2

    def test_plan_cumulative_reaches_the_total(self, session: Session, populated):
        analytics_service.recompute(session)
        plan = analytics_service.plan_payload(session)
        assert plan["cumulative_cost_lakhs"] == pytest.approx(
            sum(i["recommended_cost_lakhs"] for i in plan["items"]), abs=0.05
        )
        assert plan["is_partial"] is False

    def test_plan_size_limits_the_rows_but_flags_truncation(self, session: Session, populated):
        analytics_service.recompute(session)
        plan = analytics_service.plan_payload(session, size=1)
        assert len(plan["items"]) == 1
        assert plan["is_partial"] is True

    def test_unmeasured_factors_are_reported_in_the_summary(self, session: Session, populated):
        analytics_service.recompute(session)
        summary = analytics_service.summary_payload(session)
        assert "hotspots_with_unmeasured_factors" in summary


class TestUnscoredState:
    @pytest.mark.parametrize(
        "fn", [analytics_service.priorities_payload, analytics_service.summary_payload]
    )
    def test_reads_refuse_to_invent_a_ranking(self, session: Session, fn):
        """Empty is not zero priorities - it is no ranking, and must say so."""
        with pytest.raises(NothingScoredError):
            fn(session)

    def test_plan_also_refuses(self, session: Session):
        with pytest.raises(NothingScoredError):
            analytics_service.plan_payload(session)

    def test_health_still_answers(self, session: Session, populated):
        body = analytics_service.health_payload(session)
        assert body["status"] in {"healthy", "degraded"}
        assert body["scored"] == 0


class TestDegradedScoring:
    def test_partial_upstream_still_produces_a_ranking_with_caveats(self, tmp_path, session, monkeypatch):
        """Three of five factors beats no ranking at all."""
        path = tmp_path / "partial.db"
        conn = factory.create_tables(path, tables=["hotspots", "demand_windows", "trends"])
        factory.add_hotspot(conn, "HS-1", "Chennai", ["W1"], ["WATER"])
        factory.add_demand(conn, "W1", "WATER", 100, 5, 3.0, 10_000)
        factory.add_trend(conn, "W1", "WATER", "WORSENING", 40.0)
        conn.close()

        url = f"sqlite:///{path}"
        use_intelligence_db(monkeypatch, url)
        result = analytics_service.recompute(session)

        assert result["scored"] == 1
        assert "coverage" in result["degraded_factors"]

        items = analytics_service.priorities_payload(session)["priorities"]
        assert "coverage" in items[0]["unmeasured_factors"]
        assert "service_failure" in items[0]["unmeasured_factors"]

    def test_degraded_health_is_reported_not_hidden(self, tmp_path, session, monkeypatch):
        path = tmp_path / "partial2.db"
        conn = factory.create_tables(path, tables=["hotspots", "demand_windows", "gap_snapshots", "trends"])
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        conn.close()
        url = f"sqlite:///{path}"
        use_intelligence_db(monkeypatch, url)

        assert analytics_service.health_payload(session)["status"] == "degraded"


class TestFailureHandling:
    def test_missing_upstream_raises_and_records_a_failed_run(self, session, monkeypatch):
        use_intelligence_db(monkeypatch, "sqlite:///gone.db")
        with pytest.raises(Exception):
            analytics_service.recompute(session)
        runs = analytics_service.runs_payload(session)["runs"]
        assert any(r["status"] == "FAILED" for r in runs)

    def test_empty_snapshot_scores_nothing_without_crashing(self, tmp_path, session, monkeypatch):
        path = tmp_path / "empty_snapshot.db"
        conn = factory.create_tables(path)
        conn.close()
        url = f"sqlite:///{path}"
        use_intelligence_db(monkeypatch, url)

        result = analytics_service.recompute(session)
        assert result["scored"] == 0
        with pytest.raises(NothingScoredError):
            analytics_service.priorities_payload(session)