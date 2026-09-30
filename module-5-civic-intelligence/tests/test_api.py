"""Tests for the HTTP surface.

The API is where an analyst or Module 6 actually meets this module, so these
tests check the contract - status codes, field names, filters - rather than
re-testing the analytics, which are covered directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.enums import RiskStatus
from app.models.intelligence_tables import DemandWindow, EmergingRisk, Hotspot, Trend, WardLocation


def seed_wards(db, count=4, district="Chennai"):
    for index in range(count):
        db.add(
            WardLocation(
                ward_code=f"W{index + 1}",
                name=f"Ward {index + 1}",
                district=district,
                state="Tamil Nadu",
                latitude=13.08 + index * 0.04,
                longitude=80.27,
                population=10_000,
            )
        )
    db.flush()


def seed_two_window_history(db, ward="W1", sector="WATER", previous=10, recent=80):
    """Two windows with a large final jump, so a trend and a spike both exist."""
    now = datetime.utcnow()
    db.add_all(
        [
            DemandWindow(
                ward_code=ward, district="Chennai", sector=sector, window_days=30,
                window_start=now - timedelta(days=60), window_end=now - timedelta(days=30),
                complaint_count=previous, critical_count=1, avg_severity=3.0,
                population=10_000, source="test",
            ),
            DemandWindow(
                ward_code=ward, district="Chennai", sector=sector, window_days=30,
                window_start=now - timedelta(days=30), window_end=now,
                complaint_count=recent, critical_count=9, avg_severity=4.2,
                population=10_000, source="test",
            ),
        ]
    )
    db.flush()


class TestRoot:
    def test_root_lists_the_available_endpoints(self, client):
        payload = client.get("/").json()
        assert payload["docs"] == "/docs"
        assert payload["endpoints"]["hotspots"].endswith("/hotspots")


class TestHealth:
    def test_health_reports_the_service_and_is_healthy_when_empty(self, client):
        payload = client.get("/health").json()
        assert payload["status"] == "ok"
        assert payload["service"] == "civic-intelligence"
        assert payload["database_ready"] is False

    def test_health_counts_every_domain(self, client):
        counts = client.get("/health").json()["counts"]
        assert set(counts) == {
            "ward_locations", "demand_windows", "gap_snapshots", "project_snapshots",
            "hotspots", "trends", "emerging_risks", "runs",
        }

    def test_health_is_ready_once_wards_are_known(self, client, db):
        seed_wards(db, 2)
        assert client.get("/health").json()["database_ready"] is True

    def test_health_lists_the_districts_it_covers(self, client, db):
        seed_wards(db, 1, district="Madurai")
        assert client.get("/health").json()["districts"] == ["Madurai"]

    def test_mesh_status_explains_the_upstream(self, client):
        payload = client.get("/health/mesh").json()
        assert "mesh_database_url" in payload
        assert payload["sync_instructions"].endswith("/operations/sync")


class TestHotspotEndpoints:
    def test_empty_state_returns_an_empty_list_not_an_error(self, client):
        payload = client.get("/api/v1/hotspots").json()
        assert payload["count"] == 0
        assert payload["hotspots"] == []

    def test_summary_of_an_empty_module_is_all_zeroes(self, client):
        summary = client.get("/api/v1/hotspots/summary").json()
        assert summary["hotspot_count"] == 0
        assert summary["coverage"]["covered_share"] == 0.0

    def test_ward_count_is_derived_from_the_ward_list(self, client, db):
        db.add(
            Hotspot(
                hotspot_code="HS-1", district="Chennai", centroid_latitude=13.0,
                centroid_longitude=80.0, ward_codes=["W1", "W2"], sectors=["WATER"],
                window_days=30, total_complaints=100, critical_complaints=5,
                population=20_000, mean_intensity=5.0, peak_intensity=6.0,
                peak_severity=3.5, intensity_z_score=2.0, tier="CRITICAL",
            )
        )
        db.flush()
        payload = client.get("/api/v1/hotspots").json()["hotspots"][0]
        assert payload["ward_count"] == 2

    def test_tier_filter_narrows_the_result(self, client, db):
        for index, tier in enumerate(["CRITICAL", "NORMAL"]):
            db.add(
                Hotspot(
                    hotspot_code=f"HS-{index}", district="Chennai", centroid_latitude=13.0,
                    centroid_longitude=80.0, ward_codes=[f"W{index}"], sectors=["WATER"],
                    window_days=30, total_complaints=10, critical_complaints=0,
                    population=10_000, mean_intensity=1.0 + index, peak_intensity=1.0,
                    peak_severity=3.0, intensity_z_score=0.5, tier=tier,
                )
            )
        db.flush()
        payload = client.get("/api/v1/hotspots?tier=CRITICAL").json()
        assert payload["count"] == 1
        assert payload["hotspots"][0]["tier"] == "CRITICAL"

    def test_an_unknown_tier_is_rejected(self, client):
        assert client.get("/api/v1/hotspots?tier=CAT-ASTROPHIC").status_code == 422

    def test_limit_is_bounded(self, client):
        assert client.get("/api/v1/hotspots?limit=9999").status_code == 422

    def test_ward_brief_404s_for_an_unknown_ward(self, client):
        assert client.get("/api/v1/hotspots/wards/NOWHERE/brief").status_code == 404

    def test_ward_brief_reports_the_ward_demand(self, client, db):
        seed_wards(db, 1)
        seed_two_window_history(db, previous=10, recent=80)
        payload = client.get("/api/v1/hotspots/wards/W1/brief").json()
        assert payload["ward_code"] == "W1"
        # Only the current window counts, not the history.
        assert payload["complaints"] == 80
        assert payload["intensity"] > 0


class TestTrendEndpoints:
    def test_empty_state_returns_an_empty_list(self, client):
        assert client.get("/api/v1/trends").json() == {"count": 0, "trends": []}

    def test_trends_are_serialised_with_their_series(self, client, db):
        db.add(
            Trend(
                ward_code="W1", district="Chennai", sector="WATER", window_days=30,
                direction="WORSENING", first_count=10, last_count=80, sample_count=2,
                pct_change=700.0, slope_per_window=70.0, momentum=70.0,
                volatility=35.0, series=[10, 80],
            )
        )
        db.flush()
        payload = client.get("/api/v1/trends").json()["trends"][0]
        assert payload["direction"] == "WORSENING"
        assert payload["series"] == [10, 80]

    def test_direction_filter_narrows_the_result(self, client, db):
        for index, direction in enumerate(["WORSENING", "IMPROVING"]):
            db.add(
                Trend(
                    ward_code=f"W{index}", sector="WATER", window_days=30,
                    direction=direction, first_count=1, last_count=2, sample_count=2,
                    pct_change=10.0, slope_per_window=1.0, momentum=1.0, volatility=0.5,
                    series=[1, 2],
                )
            )
        db.flush()
        assert client.get("/api/v1/trends?direction=WORSENING").json()["count"] == 1

    def test_an_unknown_direction_is_rejected(self, client):
        assert client.get("/api/v1/trends?direction=SIDEWAYS").status_code == 422

    def test_summary_reports_every_direction_and_the_emerging_list(self, client, db):
        db.add(
            Trend(
                ward_code="W1", sector="WATER", window_days=30, direction="WORSENING",
                first_count=10, last_count=80, sample_count=2, pct_change=700.0,
                slope_per_window=70.0, momentum=70.0, volatility=35.0, series=[10, 80],
            )
        )
        db.flush()
        summary = client.get("/api/v1/trends/summary").json()
        assert summary["directions"]["WORSENING"] == 1
        assert summary["sectors"]["WATER"]["WORSENING"] == 1
        assert len(summary["emerging_sectors"]) == 1

    def test_ward_trends_are_filtered_by_ward(self, client, db):
        db.add(
            Trend(
                ward_code="W1", sector="WATER", window_days=30, direction="WORSENING",
                first_count=1, last_count=9, sample_count=2, pct_change=800.0,
                slope_per_window=8.0, momentum=8.0, volatility=1.0, series=[1, 9],
            )
        )
        db.flush()
        assert client.get("/api/v1/trends/wards/W1").json()["count"] == 1
        assert client.get("/api/v1/trends/wards/W2").json()["count"] == 0


class TestRiskEndpoints:
    def test_empty_state_returns_an_empty_list(self, client):
        assert client.get("/api/v1/risks").json() == {"count": 0, "risks": []}

    def test_a_risk_is_serialised_with_its_evidence(self, client, db):
        db.add(
            EmergingRisk(
                risk_code="SPIKE:W1:WATER", risk_type="SPIKE", rule="recent >= 2x previous",
                ward_code="W1", district="Chennai", sector="WATER", severity="HIGH",
                confidence=0.8, title="Water complaints spiking", description="details",
                evidence={"observed_ratio": 8.0}, status="OPEN",
            )
        )
        db.flush()
        payload = client.get("/api/v1/risks").json()["risks"][0]
        assert payload["evidence"]["observed_ratio"] == 8.0
        assert payload["status"] == "OPEN"

    def test_status_filter_narrows_the_result(self, client, db):
        for index, review_status in enumerate(["OPEN", "RESOLVED"]):
            db.add(
                EmergingRisk(
                    risk_code=f"R{index}", risk_type="SPIKE", rule="r", ward_code="W1",
                    severity="HIGH", confidence=0.8, title="t", description="d",
                    evidence={}, status=review_status,
                )
            )
        db.flush()
        assert client.get("/api/v1/risks?status=OPEN").json()["count"] == 1

    def test_severity_filter_narrows_the_result(self, client, db):
        db.add(
            EmergingRisk(
                risk_code="R1", risk_type="SPIKE", rule="r", ward_code="W1",
                severity="CRITICAL", confidence=0.9, title="t", description="d", evidence={},
            )
        )
        db.flush()
        assert client.get("/api/v1/risks?severity=CRITICAL").json()["count"] == 1
        assert client.get("/api/v1/risks?severity=LOW").json()["count"] == 0

    def test_status_can_be_acknowledged(self, client, db):
        db.add(
            EmergingRisk(
                risk_code="SPIKE:W1:WATER", risk_type="SPIKE", rule="r", ward_code="W1",
                severity="HIGH", confidence=0.8, title="t", description="d", evidence={},
            )
        )
        db.flush()
        payload = client.patch("/api/v1/risks/SPIKE:W1:WATER/status",
                               json={"status": "ACKNOWLEDGED"}).json()
        assert payload["status"] == "ACKNOWLEDGED"

    def test_acknowledging_an_unknown_risk_is_a_404(self, client):
        response = client.patch("/api/v1/risks/NOPE/status", json={"status": "ACKNOWLEDGED"})
        assert response.status_code == 404

    def test_an_invalid_status_is_rejected(self, client, db):
        db.add(
            EmergingRisk(
                risk_code="R1", risk_type="SPIKE", rule="r", severity="HIGH",
                confidence=0.8, title="t", description="d", evidence={},
            )
        )
        db.flush()
        response = client.patch("/api/v1/risks/R1/status", json={"status": "MAYBE"})
        assert response.status_code == 422

    def test_summary_counts_open_and_total_separately(self, client, db):
        for index, review_status in enumerate(["OPEN", "RESOLVED"]):
            db.add(
                EmergingRisk(
                    risk_code=f"R{index}", risk_type="SPIKE", rule="r", ward_code="W1",
                    sector="WATER", severity="HIGH", confidence=0.8, title="t",
                    description="d", evidence={}, status=review_status,
                )
            )
        db.flush()
        summary = client.get("/api/v1/risks/summary").json()
        assert summary["total"] == 2
        assert summary["open"] == 1
        assert summary["by_type"]["SPIKE"] == 2

    def test_ward_risks_returns_only_open_ones(self, client, db):
        for index, review_status in enumerate(["OPEN", "DISMISSED"]):
            db.add(
                EmergingRisk(
                    risk_code=f"R{index}", risk_type="SPIKE", rule="r", ward_code="W1",
                    sector="WATER", severity="HIGH", confidence=0.8, title="t",
                    description="d", evidence={}, status=review_status,
                )
            )
        db.flush()
        assert client.get("/api/v1/risks/wards/W1").json()["count"] == 1


class TestOperations:
    def test_sync_without_a_mesh_is_a_503_with_a_remedy(self, client):
        # 503 rather than a silent success: nothing was synced, and the caller
        # needs to know the mesh has to be seeded first.
        response = client.post("/api/v1/operations/sync")
        assert response.status_code == 503
        assert "seed_mesh.py" in response.json()["detail"]

    def test_analysis_of_an_empty_module_succeeds_with_zeroes(self, client):
        payload = client.post("/api/v1/operations/analyse").json()
        assert payload["hotspots"] == 0
        assert payload["trends"] == 0
        assert payload["risks"] == 0
        assert payload["run_id"] >= 1

    def test_analysis_produces_a_risk_from_a_spike(self, client, db):
        seed_wards(db, 1)
        seed_two_window_history(db, previous=10, recent=80)
        payload = client.post("/api/v1/operations/analyse").json()
        assert payload["risks"] == 1
        assert payload["risks_summary"]["by_type"]["SPIKE"] == 1

    def test_analysis_records_a_run_the_history_can_show(self, client, db):
        seed_wards(db, 1)
        seed_two_window_history(db)
        client.post("/api/v1/operations/analyse")
        runs = client.get("/api/v1/operations/runs").json()
        assert len(runs) >= 1
        assert runs[0]["wards_analysed"] == 1

    def test_runs_are_empty_before_anything_has_run(self, client):
        assert client.get("/api/v1/operations/runs").json() == []

    def test_an_invalid_window_is_rejected(self, client):
        assert client.post("/api/v1/operations/analyse?window_days=0").status_code == 422


class TestAnalyticsEndpointsTogether:
    def test_a_full_pass_exposes_consistent_numbers_everywhere(self, client, db):
        # The three summaries must agree with each other, or a dashboard built on
        # them will contradict itself.
        seed_wards(db, 3)
        seed_two_window_history(db, previous=10, recent=80)
        analysis = client.post("/api/v1/operations/analyse").json()
        hotspots = client.get("/api/v1/hotspots").json()
        risks = client.get("/api/v1/risks").json()

        assert analysis["hotspots"] == hotspots["count"]
        assert analysis["risks"] == risks["count"]

    def test_hotspot_summary_coverage_matches_the_hotspot_list(self, client, db):
        seed_wards(db, 3)
        seed_two_window_history(db)
        client.post("/api/v1/operations/analyse")
        summary = client.get("/api/v1/hotspots/summary").json()
        assert summary["hotspot_count"] == summary["coverage"]["hotspot_count"]
        assert summary["tiers"]["CRITICAL"] + summary["tiers"]["HIGH"] + \
            summary["tiers"]["MODERATE"] + summary["tiers"]["NORMAL"] == summary["hotspot_count"]