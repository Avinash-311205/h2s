"""HTTP contract, including the status codes that carry meaning."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import use_intelligence_db


@pytest.fixture
def scored_client(client: TestClient, intelligence_db, monkeypatch) -> TestClient:
    """A client with an intelligence snapshot that has already been scored."""
    import synthetic_snapshot as factory

    conn = factory.create_tables(intelligence_db)
    factory.add_hotspot(conn, "HS-1", "Chennai", ["W1"], ["WATER"], 10_000, 200, 12)
    factory.add_demand(conn, "W1", "WATER", 200, 12, 3.5, 10_000)
    factory.add_gap(conn, "W1", "WATER", 55.0)
    factory.add_project(conn, "P1", "W1", "WATER", "STALLED", 200.0, 30.0, 120)
    factory.add_trend(conn, "W1", "WATER", "WORSENING", 55.0)
    conn.close()
    use_intelligence_db(monkeypatch, f"sqlite:///{intelligence_db}")
    response = client.post("/api/v1/operations/recompute")
    assert response.status_code == 200, response.text
    return client


class TestHealth:
    def test_health_reports_service_identity(self, client: TestClient):
        body = client.get("/health").json()
        assert body["service"] == "module-6-priority-recommendation"
        assert body["status"] in {"healthy", "degraded", "unavailable"}

    def test_health_works_before_anything_is_scored(self, client: TestClient):
        """Health must answer even with no ranking, or an operator cannot triage."""
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["scored"] == 0

    def test_health_reports_unavailable_when_upstream_is_missing(self, client, monkeypatch):
        use_intelligence_db(monkeypatch, "sqlite:///definitely/not/here.db")
        body = client.get("/health").json()
        assert body["intelligence_available"] is False
        assert body["status"] == "unavailable"


class TestNotScored:
    """409 is its own thing: the service is fine, the ranking does not exist."""

    @pytest.mark.parametrize(
        "path", ["/api/v1/priorities", "/api/v1/priorities/summary", "/api/v1/plan"]
    )
    def test_reads_return_409_before_a_recompute(self, client: TestClient, path):
        response = client.get(path)
        assert response.status_code == 409
        assert "recompute" in response.json()["detail"].lower()


class TestRecompute:
    def test_recompute_scores_and_reports_source_and_run_id(self, scored_client):
        body = scored_client.post("/api/v1/operations/recompute").json()
        assert body["scored"] == 1
        assert body["run_id"] > 0
        assert "intelligence.db" in body["source"]
        assert body["duration_ms"] >= 0

    def test_recompute_takes_no_body(self, scored_client):
        assert scored_client.post("/api/v1/operations/recompute").status_code == 200


class TestPriorities:
    def test_ranking_is_returned_with_evidence(self, scored_client):
        items = scored_client.get("/api/v1/priorities").json()["priorities"]
        assert len(items) == 1
        item = items[0]
        assert item["band"] in {"HIGH", "MEDIUM", "LOW"}
        assert set(item["factors"]) == {
            "demand",
            "severity",
            "trend",
            "coverage",
            "service_failure",
        }
        assert item["recommendation"]

    def test_all_three_bands_are_always_reported(self, scored_client):
        bands = scored_client.get("/api/v1/priorities").json()["bands"]
        assert set(bands) == {"HIGH", "MEDIUM", "LOW"}

    def test_district_filter_narrows_results(self, scored_client):
        assert scored_client.get("/api/v1/priorities?district=Chennai").json()["count"] == 1
        assert scored_client.get("/api/v1/priorities?district=Kochi").json()["count"] == 0

    def test_band_filter_narrows_results(self, scored_client):
        for band in ("HIGH", "MEDIUM", "LOW"):
            body = scored_client.get(f"/api/v1/priorities?band={band}").json()
            assert all(i["band"] == band for i in body["priorities"])

    def test_unknown_band_is_rejected(self, scored_client):
        assert scored_client.get("/api/v1/priorities?band=URGENT").status_code == 422


class TestSummaryAndPlan:
    def test_summary_totals_are_consistent(self, scored_client):
        summary = scored_client.get("/api/v1/priorities/summary").json()
        priorities = scored_client.get("/api/v1/priorities").json()["priorities"]
        assert summary["scored"] == len(priorities)
        assert summary["population_covered"] == sum(p["population"] for p in priorities)
        assert summary["complaints_covered"] == sum(p["total_complaints"] for p in priorities)

    def test_plan_cumulative_cost_is_inclusive(self, scored_client):
        plan = scored_client.get("/api/v1/plan").json()
        assert plan["cumulative_cost_lakhs"] == plan["total_cost_lakhs"]
        assert plan["items"][-1]["cumulative_cost_lakhs"] == plan["cumulative_cost_lakhs"]

    def test_plan_size_is_validated(self, scored_client):
        assert scored_client.get("/api/v1/plan?size=0").status_code == 422
        assert scored_client.get("/api/v1/plan?size=999").status_code == 422


class TestHistoryAndRuns:
    def test_history_for_unknown_hotspot_is_404(self, scored_client):
        assert scored_client.get("/api/v1/priorities/NOPE/history").status_code == 404

    def test_first_run_has_no_history_yet(self, scored_client):
        body = scored_client.get("/api/v1/priorities/HS-1/history").json()
        assert body["current"]["rank"] == 1
        assert body["entries"] == []

    def test_second_run_records_history_and_movement(self, scored_client):
        scored_client.post("/api/v1/operations/recompute")
        body = scored_client.get("/api/v1/priorities/HS-1/history").json()
        assert len(body["entries"]) == 1

        item = scored_client.get("/api/v1/priorities").json()["priorities"][0]
        assert item["movement"]["previous_score"] is not None
        assert item["movement"]["score_change"] == pytest.approx(0.0)

    def test_runs_are_recorded_with_status(self, scored_client):
        runs = scored_client.get("/api/v1/operations/runs").json()["runs"]
        assert runs
        assert runs[0]["status"] in {"SUCCESS", "RUNNING", "FAILED"}

    def test_run_records_include_error_field(self, scored_client):
        run = scored_client.get("/api/v1/operations/runs").json()["runs"][0]
        assert "error" in run


class TestRecomputeFailure:
    def test_missing_upstream_is_503_and_does_not_destroy_the_ranking(self, client, monkeypatch, scored_client):
        """A failed recompute must leave the previous ranking intact."""
        before = client.get("/api/v1/priorities").json()["count"]

        use_intelligence_db(monkeypatch, "sqlite:///gone/missing.db")
        response = client.post("/api/v1/operations/recompute")
        assert response.status_code == 503
        assert client.get("/api/v1/priorities").json()["count"] == before

    def test_failed_run_is_recorded(self, client, monkeypatch, scored_client):
        use_intelligence_db(monkeypatch, "sqlite:///gone/missing.db")
        client.post("/api/v1/operations/recompute")
        runs = client.get("/api/v1/operations/runs").json()["runs"]
        assert any(r["status"] == "FAILED" for r in runs)
        assert any(r["error"] for r in runs)