"""Tests for the repository and the sync/analysis pipeline.

The guarantees under test are the ones a reviewer would otherwise have to take on
trust: a re-sync inserts nothing, project history records only real changes, and
an analyst's review decision survives the next analytics run.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.enums import RiskStatus
from app.models.intelligence_tables import (
    DemandWindow,
    EmergingRisk,
    GapSnapshot,
    Hotspot,
    IntelligenceRun,
    ProjectSnapshot,
    Trend,
    WardLocation,
)
from app.repositories.intelligence_repository import IntelligenceRepository
from app.services import analytics_service, hotspot_service, mesh_client
from app.services.hotspot_service import HotspotResult, WardSignal
from app.services.risk_service import RiskSignal


def mesh_snapshot(*, wards=2, demand=3, gaps=2, projects=1, ward_prefix="W"):
    """A synthetic mesh payload in the shape the client returns."""
    ward_rows = [
        {
            "ward_code": f"{ward_prefix}{index + 1}",
            "name": f"Ward {index + 1}",
            "district": "Chennai",
            "state": "Tamil Nadu",
            "centroid_latitude": 13.08 + index * 0.05,
            "centroid_longitude": 80.27,
            "population": 10_000 + index * 1_000,
        }
        for index in range(wards)
    ]
    demand_rows = [
        {
            "ward_code": f"{ward_prefix}{index % wards + 1}",
            # Vary the sector too, so every row has a distinct natural key
            # (ward, sector, window bounds) and none is silently de-duplicated.
            "sector": ["WATER", "ROAD", "HEALTHCARE"][index % 3],
            "window_days": 30,
            "complaint_count": 20 + index * 5,
            "critical_count": 2,
            "avg_severity": 3.2,
            "observed_from": "2026-06-01 00:00:00",
            "observed_to": "2026-07-01 00:00:00",
        }
        for index in range(demand)
    ]
    gap_rows = [
        {
            "ward_code": f"{ward_prefix}{index + 1}",
            "sector": "WATER",
            "gap_score": 80.0 + index,
            "severity": "CRITICAL",
            "demand_score": 40.0,
            "absence_score": 25.0,
            "quality_score": 15.0,
            "recommended_action": "URGENT_REPAIR",
            "active_project_count": 1,
            "computed_at": "2026-07-01 00:00:00",
        }
        for index in range(gaps)
    ]
    project_rows = [
        {
            "project_code": f"P-{index + 1}",
            "ward_code": f"{ward_prefix}1",
            "sector": "WATER",
            "status": "IN_PROGRESS",
            "title": f"Water works {index + 1}",
            "budget_lakhs": 500.0,
            "spent_lakhs": 50.0,
            "sanctioned_on": "2025-01-01",
            "expected_completion_on": "2026-12-31",
            "delay_days": 0,
        }
        for index in range(projects)
    ]
    return mesh_client.MeshSnapshot(
        wards=ward_rows, demand=demand_rows, gaps=gap_rows, projects=project_rows
    )


class TestStoreSnapshot:
    def test_a_fresh_snapshot_inserts_everything(self, db):
        inserted = IntelligenceRepository(db).store_snapshot(mesh_snapshot())
        assert inserted == {"wards": 2, "demand": 3, "gaps": 2, "projects": 1}

    def test_the_same_snapshot_twice_inserts_nothing_the_second_time(self, db):
        # This is what makes sync safe to run on a schedule: an unchanged mesh
        # must not inflate the history that trends are computed over.
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        assert repo.store_snapshot(mesh_snapshot()) == {
            "wards": 0, "demand": 0, "gaps": 0, "projects": 0,
        }

    def test_wards_are_updated_in_place_not_duplicated(self, db):
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        snapshot = mesh_snapshot()
        snapshot.wards[0]["population"] = 99_000
        repo.store_snapshot(snapshot)
        db.flush()
        assert len(repo.list_ward_locations()) == 2
        assert repo.list_ward_locations()[0].population == 99_000

    def test_demand_rows_inherit_the_ward_district(self, db):
        # Snapshots carry the district so risk rules do not need a join back.
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        assert {row.district for row in repo.list_demand()} == {"Chennai"}

    def test_a_different_window_is_a_new_row(self, db):
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        later = mesh_snapshot(demand=0)
        later.demand = [
            {
                "ward_code": "W1", "sector": "WATER", "window_days": 30,
                "complaint_count": 99, "critical_count": 1, "avg_severity": 3.0,
                "observed_from": "2026-07-01 00:00:00", "observed_to": "2026-08-01 00:00:00",
            }
        ]
        repo.store_snapshot(later)
        db.flush()
        assert len(repo.list_demand()) == 4

    def test_rows_without_a_ward_code_are_skipped(self, db):
        snapshot = mesh_snapshot(wards=0, demand=0, gaps=0, projects=0)
        snapshot.demand = [{"ward_code": None, "sector": "WATER", "complaint_count": 5}]
        assert IntelligenceRepository(db).store_snapshot(snapshot)["demand"] == 0


class TestProjectHistory:
    def test_an_unchanged_project_is_not_resnapshotted(self, db):
        # Snapshotting on every sync would record a history that never changes,
        # which is noise dressed up as an audit trail.
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        assert repo.store_snapshot(mesh_snapshot())["projects"] == 0

    def test_new_spend_is_recorded_as_a_new_snapshot(self, db):
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        snapshot = mesh_snapshot(projects=1)
        snapshot.projects[0]["spent_lakhs"] = 275.0
        assert repo.store_snapshot(snapshot)["projects"] == 1

    def test_a_status_change_is_recorded(self, db):
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        snapshot = mesh_snapshot(projects=1)
        snapshot.projects[0]["status"] = "COMPLETED"
        assert repo.store_snapshot(snapshot)["projects"] == 1

    def test_a_revised_completion_date_is_recorded(self, db):
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot())
        db.flush()
        snapshot = mesh_snapshot(projects=1)
        snapshot.projects[0]["expected_completion_on"] = "2027-12-31"
        assert repo.store_snapshot(snapshot)["projects"] == 1

    def test_list_projects_returns_the_latest_state_per_project(self, db):
        repo = IntelligenceRepository(db)
        repo.store_snapshot(mesh_snapshot(projects=2))
        db.flush()
        snapshot = mesh_snapshot(projects=1)
        snapshot.projects[0]["spent_lakhs"] = 400.0
        repo.store_snapshot(snapshot)
        db.flush()
        projects = {p.project_code: p.spent_lakhs for p in repo.list_projects()}
        assert projects == {"P-1": 400.0, "P-2": 50.0}


class TestLatestReads:
    def test_latest_gaps_keeps_only_the_newest_snapshot_per_ward_sector(self, db):
        db.add_all(
            [
                GapSnapshot(ward_code="W1", sector="WATER", snapshot_at=datetime(2026, 6, 1),
                            gap_score=50.0, severity="MODERATE"),
                GapSnapshot(ward_code="W1", sector="WATER", snapshot_at=datetime(2026, 7, 1),
                            gap_score=85.0, severity="CRITICAL"),
            ]
        )
        db.flush()
        latest = IntelligenceRepository(db).latest_gaps()
        assert len(latest) == 1
        assert latest[0].gap_score == pytest.approx(85.0)

    def test_list_demand_filters_by_window_and_sector(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1"),
            make_demand("W1", "WATER", 10, window_days=30),
            make_demand("W1", "WATER", 20, window_days=90, end_days_ago=90),
            make_demand("W1", "ROAD", 30, window_days=30),
        ])
        db.flush()
        repo = IntelligenceRepository(db)
        assert len(repo.list_demand(window_days=30, sector="WATER")) == 1
        assert len(repo.list_demand(window_days=30)) == 2

    def test_list_demand_respects_the_limit(self, db, make_ward, make_demand):
        db.add_all([make_ward("W1")] + [make_demand("W1", "WATER", i + 1) for i in range(5)])
        db.flush()
        assert len(IntelligenceRepository(db).list_demand(limit=3)) == 3


class TestRiskPersistence:
    def signal(self, ward="W1", sector="WATER"):
        return RiskSignal(
            risk_type="SPIKE", rule="recent >= 2x previous", ward_code=ward,
            district="Chennai", sector=sector, severity="HIGH", confidence=0.8,
            title="Water complaints spiking", description="...", evidence={"ratio": 3.0},
        )

    def test_risks_are_persisted_with_a_stable_code(self, db):
        IntelligenceRepository(db).replace_risks([self.signal()], run_id=1)
        db.flush()
        row = db.scalar(select(EmergingRisk))
        assert row.risk_code == "SPIKE:W1:WATER"
        assert row.status == RiskStatus.OPEN.value

    def test_re_detection_updates_rather_than_duplicates(self, db):
        repo = IntelligenceRepository(db)
        repo.replace_risks([self.signal()], run_id=1)
        db.flush()
        repo.replace_risks([self.signal()], run_id=2)
        db.flush()
        assert db.query(EmergingRisk).count() == 1

    def test_a_review_decision_survives_the_next_run(self, db):
        # An officer's acknowledgement must not be undone by a nightly re-run.
        repo = IntelligenceRepository(db)
        repo.replace_risks([self.signal()], run_id=1)
        db.flush()
        repo.set_risk_status("SPIKE:W1:WATER", RiskStatus.ACKNOWLEDGED.value)
        db.flush()
        repo.replace_risks([self.signal()], run_id=2)
        db.flush()
        row = db.scalar(select(EmergingRisk))
        assert row.status == RiskStatus.ACKNOWLEDGED.value
        assert row.run_id == 2

    def test_evidence_is_refreshed_on_re_detection(self, db):
        repo = IntelligenceRepository(db)
        repo.replace_risks([self.signal()], run_id=1)
        db.flush()
        updated = self.signal()
        updated.evidence = {"ratio": 5.0}
        repo.replace_risks([updated], run_id=2)
        db.flush()
        assert db.scalar(select(EmergingRisk)).evidence == {"ratio": 5.0}

    def test_open_count_ignores_reviewed_risks(self, db):
        repo = IntelligenceRepository(db)
        repo.replace_risks([self.signal("W1"), self.signal("W2")], run_id=1)
        db.flush()
        repo.set_risk_status("SPIKE:W1:WATER", RiskStatus.RESOLVED.value)
        db.flush()
        assert repo.open_risk_count() == 1

    def test_setting_status_on_an_unknown_code_returns_nothing(self, db):
        assert IntelligenceRepository(db).set_risk_status("NOPE", "OPEN") is None

    def test_risks_are_ordered_by_severity_then_confidence(self, db):
        db.add_all([
            EmergingRisk(risk_code="A", risk_type="SPIKE", rule="r", severity="MEDIUM",
                         confidence=0.9, title="t", description="d", evidence={}),
            EmergingRisk(risk_code="B", risk_type="SPIKE", rule="r", severity="CRITICAL",
                         confidence=0.5, title="t", description="d", evidence={}),
        ])
        db.flush()
        assert [r.risk_code for r in IntelligenceRepository(db).list_risks()] == ["B", "A"]


class TestTrendPersistence:
    def test_trends_inherit_the_ward_district(self, db, make_ward):
        from app.services.trend_service import TrendResult

        db.add(make_ward("W1", district="Madurai"))
        db.flush()
        IntelligenceRepository(db).replace_trends(
            [TrendResult("W1", "WATER", 30, "WORSENING", 10, 40, 3, 300.0, 15.0, 30.0, 4.0)]
        )
        db.flush()
        assert db.scalar(select(Trend)).district == "Madurai"

    def test_trends_upsert_on_their_grain(self, db):
        from app.services.trend_service import TrendResult

        repo = IntelligenceRepository(db)
        repo.replace_trends([TrendResult("W1", "WATER", 30, "WORSENING", 10, 40, 3,
                                         300.0, 15.0, 30.0, 4.0, [10, 20, 40])])
        db.flush()
        repo.replace_trends([TrendResult("W1", "WATER", 30, "STABLE", 10, 12, 3,
                                         20.0, 1.0, 2.0, 1.0, [10, 11, 12])])
        db.flush()
        rows = db.query(Trend).all()
        assert len(rows) == 1
        assert rows[0].direction == "STABLE"
        assert rows[0].series == [10, 11, 12]


class TestHotspotPersistence:
    def test_hotspots_are_replaced_per_window(self, db):
        repo = IntelligenceRepository(db)
        first = HotspotResult("Chennai", 13.0, 80.0, ["W1"], ["WATER"], 10_000, 100, 5,
                              10.0, 12.0, 3.0, 2.0, "HIGH", 30)
        repo.replace_hotspots([first], window_days=30)
        db.flush()
        repo.replace_hotspots([first], window_days=30)
        db.flush()
        assert db.query(Hotspot).count() == 1

    def test_hotspots_carry_their_window_bounds(self, db):
        hotspot = HotspotResult("Chennai", 13.0, 80.0, ["W1"], ["WATER"], 10_000, 100, 5,
                                10.0, 12.0, 3.0, 2.0, "HIGH", 30)
        IntelligenceRepository(db).replace_hotspots([hotspot], window_days=30)
        db.flush()
        row = db.scalar(select(Hotspot))
        assert row.window_start is not None and row.window_end is not None
        assert (row.window_end - row.window_start).days == 30

    def test_hotspots_are_listed_worst_first(self, db):
        repo = IntelligenceRepository(db)
        repo.replace_hotspots(
            [
                HotspotResult("A", 13.0, 80.0, ["W1"], ["WATER"], 10_000, 10, 1, 1.0, 2.0, 3.0, 0.1, "NORMAL", 30),
                HotspotResult("B", 14.0, 80.0, ["W2"], ["ROAD"], 10_000, 90, 9, 9.0, 10.0, 3.0, 2.0, "CRITICAL", 30),
            ],
            window_days=30,
        )
        db.flush()
        assert [row.district for row in repo.list_hotspots()] == ["B", "A"]


class TestRunsAndCounts:
    def test_a_run_records_what_it_produced(self, db):
        repo = IntelligenceRepository(db)
        run = repo.start_run(kind="analysis", window_days=30)
        repo.finish_run(run, wards_analysed=16, hotspots_created=6, duration_ms=12)
        db.flush()
        row = db.scalar(select(IntelligenceRun))
        assert (row.wards_analysed, row.hotspots_created, row.duration_ms) == (16, 6, 12)

    def test_runs_are_listed_newest_first(self, db):
        repo = IntelligenceRepository(db)
        older = repo.start_run()
        repo.finish_run(older, run_at=datetime(2026, 1, 1))
        newer = repo.start_run()
        repo.finish_run(newer, run_at=datetime(2026, 7, 1))
        db.flush()
        assert repo.list_runs()[0].id == newer.id

    def test_counts_cover_every_table(self, db):
        counts = IntelligenceRepository(db).counts()
        assert set(counts) == {
            "ward_locations", "demand_windows", "gap_snapshots", "project_snapshots",
            "hotspots", "trends", "emerging_risks", "runs",
        }


class TestAnalyticsPipeline:
    def test_signals_are_built_from_the_selected_window_only(self, db, make_ward, make_demand):
        # A 90-day total must never leak into a 30-day hotspot.
        db.add_all([
            make_ward("W1", population=10_000),
            make_ward("W2", population=10_000),
            make_demand("W1", "WATER", 100, window_days=30),
            make_demand("W1", "WATER", 5_000, window_days=90, end_days_ago=90),
        ])
        db.flush()
        signals = analytics_service.build_ward_signals(
            IntelligenceRepository(db), window_days=30
        )
        assert next(s for s in signals if s.ward_code == "W1").complaints == 100

    def test_only_the_latest_window_counts_towards_a_hotspot(self, db, make_ward, make_demand):
        # Two consecutive 30-day windows share a window_days value. Counting both
        # would make a hotspot's totals grow every time a new window is synced.
        db.add_all([
            make_ward("W1", population=10_000),
            make_demand("W1", "WATER", 20, window_days=30, end_days_ago=30),
            make_demand("W1", "WATER", 80, window_days=30),
        ])
        db.flush()
        signals = analytics_service.build_ward_signals(
            IntelligenceRepository(db), window_days=30
        )
        assert next(s for s in signals if s.ward_code == "W1").complaints == 80

    def test_the_earlier_window_is_chosen_when_it_is_the_only_one(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1", population=10_000),
            make_demand("W1", "WATER", 33, window_days=30, end_days_ago=30),
        ])
        db.flush()
        signals = analytics_service.build_ward_signals(
            IntelligenceRepository(db), window_days=30
        )
        assert next(s for s in signals if s.ward_code == "W1").complaints == 33

    def test_latest_demand_keeps_one_row_per_ward_sector_and_window(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1"),
            make_demand("W1", "WATER", 10, window_days=30, end_days_ago=30),
            make_demand("W1", "WATER", 20, window_days=30),
            make_demand("W1", "WATER", 30, window_days=90, end_days_ago=90),
            make_demand("W1", "WATER", 40, window_days=90),
        ])
        db.flush()
        rows = IntelligenceRepository(db).list_latest_demand()
        assert {(row.complaint_count, row.window_days) for row in rows} == {(20, 30), (40, 90)}

    def test_signal_peak_severity_is_the_worst_window(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1"),
            make_demand("W1", "WATER", 10, window_days=30, avg_severity=2.0),
            make_demand("W1", "ROAD", 10, window_days=30, avg_severity=4.5),
        ])
        db.flush()
        signal = analytics_service.build_ward_signals(IntelligenceRepository(db))[0]
        assert signal.peak_severity == pytest.approx(4.5)

    def test_signal_sectors_are_collected_without_duplicates(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1"),
            make_demand("W1", "WATER", 5),
            make_demand("W1", "ROAD", 5),
        ])
        db.flush()
        assert sorted(analytics_service.build_ward_signals(
            IntelligenceRepository(db))[0].sectors) == ["ROAD", "WATER"]

    def test_elapsed_fraction_is_bounded_to_the_project_window(self):
        today = datetime.utcnow().date()
        project = ProjectSnapshot(
            project_code="P1", ward_code="W1", sector="WATER", status="IN_PROGRESS",
            sanctioned_on=datetime(today.year, today.month, 1),
            expected_completion_on=datetime(today.year + 2, 1, 1),
        )
        fraction = analytics_service.project_elapsed_fractions([project])["P1"]
        assert 0.0 <= fraction <= 1.0

    def test_a_project_with_no_dates_has_no_elapsed_fraction(self):
        project = ProjectSnapshot(project_code="P1", ward_code="W1", sector="WATER",
                                  status="IN_PROGRESS")
        assert analytics_service.project_elapsed_fractions([project]) == {}

    def test_analysis_over_an_empty_database_produces_nothing_but_a_run(self, db):
        result = analytics_service.analyse(db)
        assert (result.hotspots, result.trends, result.risks) == (0, 0, 0)
        assert IntelligenceRepository(db).counts()["runs"] == 1

    def test_analysis_persists_its_outputs(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1", population=8_000),
            make_demand("W1", "WATER", 20, end_days_ago=30),
            make_demand("W1", "WATER", 60),
        ])
        db.flush()
        result = analytics_service.analyse(db)
        assert result.hotspots == 1
        assert IntelligenceRepository(db).counts()["hotspots"] == 1

    def test_re_running_analysis_does_not_duplicate_outputs(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1", population=8_000),
            make_demand("W1", "WATER", 20, end_days_ago=30),
            make_demand("W1", "WATER", 60),
        ])
        db.flush()
        analytics_service.analyse(db)
        first = IntelligenceRepository(db).counts()["hotspots"]
        analytics_service.analyse(db)
        assert IntelligenceRepository(db).counts()["hotspots"] == first

    def test_a_worse_latest_window_shows_up_as_a_risk(self, db, make_ward, make_demand):
        db.add_all([
            make_ward("W1"),
            make_demand("W1", "WATER", 10, end_days_ago=30),
            make_demand("W1", "WATER", 90),
        ])
        db.flush()
        result = analytics_service.analyse(db)
        assert result.risk_summary["by_type"]["SPIKE"] == 1


class TestSyncWithoutMesh:
    def test_sync_reports_the_mesh_unavailable_rather_than_raising(self, db, tmp_path):
        # A missing mesh must not take the module down: it can still serve what it
        # has already synced.
        result = analytics_service.sync_from_mesh(db, database_url=f"sqlite:///{tmp_path}/none.db")
        assert result.mesh_available is False
        assert result.counts == {}

    def test_sync_and_analyse_skips_analysis_without_a_mesh(self, db, tmp_path):
        result = analytics_service.sync_and_analyse(
            db, database_url=f"sqlite:///{tmp_path}/none.db"
        )
        assert result["analysis"] is None
        assert result["sync"]["mesh_available"] is False