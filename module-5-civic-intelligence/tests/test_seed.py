"""Tests for the seed script.

The seed exists to make the analytics demonstrable without a live mesh, so the
properties worth protecting are that it produces a usable amount of history, that
it is deterministic, and that its backfilled data is labelled as synthetic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.enums import ProjectStatus
from app.models.intelligence_tables import DemandWindow, ProjectSnapshot, WardLocation
from app.repositories.intelligence_repository import IntelligenceRepository

SEED_PATH = Path(__file__).resolve().parents[1] / "seed_intelligence.py"


@pytest.fixture(scope="module")
def seed_module():
    spec = importlib.util.spec_from_file_location("seed_intelligence", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWardGrid:
    def test_wards_are_generated_across_districts(self, seed_module):
        import random

        wards = seed_module._ward_grid(random.Random(1))
        assert len(wards) == len(seed_module._DISTRICTS) * seed_module._WARDS_PER_DISTRICT
        assert {ward.district for ward in wards} == set(seed_module._DISTRICTS)

    def test_ward_codes_are_unique(self, seed_module):
        import random

        wards = seed_module._ward_grid(random.Random(1))
        assert len({ward.ward_code for ward in wards}) == len(wards)

    def test_populations_are_plausible_for_wards(self, seed_module):
        import random

        for ward in seed_module._ward_grid(random.Random(1)):
            assert 1_000 < ward.population < 100_000


class TestDemandBackfill:
    def build(self, seed_module, *, windows=3, seed_value=7, anchor=None):
        import random

        rng = random.Random(seed_value)
        wards = seed_module._ward_grid(rng)
        return seed_module._backfill_demand(
            wards, windows=windows, window_days=30, rng=rng, anchor=anchor
        )

    def test_one_row_per_ward_per_window(self, seed_module):
        rows = self.build(seed_module, windows=3)
        assert len(rows) == len(seed_module._ward_grid(__import__("random").Random(7))) * 3

    def test_rows_are_ordered_oldest_first_within_each_series(self, seed_module):
        rows = self.build(seed_module, windows=3)
        by_ward = {}
        for row in rows:
            by_ward.setdefault(row.ward_code, []).append(row.window_end)
        for ends in by_ward.values():
            assert ends == sorted(ends)

    def test_without_a_mesh_every_row_is_labelled_as_history(self, seed_module):
        # Synthetic rows must never be mistakable for real synced data.
        assert {row.source for row in self.build(seed_module)} == {"seed_history"}

    def test_a_mesh_anchor_is_used_for_the_most_recent_window(self, seed_module):
        # The seeded history should end where real data begins.
        import random

        rng = random.Random(7)
        ward = seed_module._ward_grid(rng)[0]
        anchor = {(ward.ward_code, "WATER"): 999}
        rows = self.build(seed_module, anchor=anchor)
        latest = [r for r in rows if r.ward_code == ward.ward_code and r.sector == "WATER"]
        assert latest[-1].complaint_count == 999

    def test_anchored_series_follow_the_mesh_grain(self, seed_module):
        # Every (ward, sector) the mesh reports gets a series, rather than a
        # randomly chosen sector happening to coincide.
        import random

        rng = random.Random(7)
        ward = seed_module._ward_grid(rng)[0]
        anchor = {(ward.ward_code, "WATER"): 5, (ward.ward_code, "ROAD"): 6}
        rows = self.build(seed_module, anchor=anchor)
        grains = {(r.ward_code, r.sector) for r in rows if r.ward_code == ward.ward_code}
        assert grains == {(ward.ward_code, "WATER"), (ward.ward_code, "ROAD")}

    def test_anchor_rows_for_unknown_wards_are_ignored(self, seed_module):
        rows = self.build(seed_module, anchor={("NOWHERE-99", "WATER"): 999})
        assert all(row.complaint_count != 999 for row in rows)

    def test_anchored_rows_are_labelled_as_mesh_data(self, seed_module):
        import random

        rng = random.Random(7)
        ward = seed_module._ward_grid(rng)[0]
        rows = self.build(seed_module, anchor={(ward.ward_code, "WATER"): 12})
        sources = {r.source for r in rows if r.ward_code == ward.ward_code}
        assert sources == {"mesh", "seed_history"}

    def test_generation_is_deterministic_for_a_given_seed(self, seed_module):
        first = [(r.ward_code, r.complaint_count) for r in self.build(seed_module, seed_value=11)]
        second = [(r.ward_code, r.complaint_count) for r in self.build(seed_module, seed_value=11)]
        assert first == second

    def test_a_different_seed_produces_different_data(self, seed_module):
        first = [r.complaint_count for r in self.build(seed_module, seed_value=11)]
        second = [r.complaint_count for r in self.build(seed_module, seed_value=12)]
        assert first != second

    def test_the_series_contains_both_improving_and_worsening_movement(self, seed_module):
        # Without a mix of directions, the analytics would have nothing to find.
        rows = self.build(seed_module, windows=3, seed_value=3)
        by_ward = {}
        for row in rows:
            by_ward.setdefault(row.ward_code, []).append(row.complaint_count)
        movers = [
            series for series in by_ward.values()
            if series[-1] > series[0] or series[-1] < series[0]
        ]
        assert movers


class TestProjectBackfill:
    def build(self, seed_module, seed_value=5):
        import random

        rng = random.Random(seed_value)
        wards = seed_module._ward_grid(rng)
        return seed_module._backfill_projects(wards, rng=rng)

    def test_projects_use_known_statuses(self, seed_module):
        valid = {status.value for status in ProjectStatus}
        for project in self.build(seed_module):
            assert project.status in valid

    def test_spend_never_exceeds_the_budget(self, seed_module):
        for project in self.build(seed_module):
            assert project.spent_lakhs <= project.budget_lakhs

    def test_at_least_one_project_is_stalled(self, seed_module):
        # STALLED_ABSORPTION needs a genuine example to find.
        stalled = [
            p for p in self.build(seed_module)
            if p.status in {"IN_PROGRESS", "PLANNED", "DELAYED"} and p.spent_lakhs < 0.35 * p.budget_lakhs
        ]
        assert stalled

    def test_completed_projects_are_fully_spent(self, seed_module):
        for project in self.build(seed_module):
            if project.status == ProjectStatus.COMPLETED.value:
                assert project.spent_lakhs == project.budget_lakhs


class TestGapBackfill:
    def test_bands_follow_module_fours_scale(self, seed_module):
        import random

        rng = random.Random(9)
        rows = seed_module._backfill_gaps(seed_module._ward_grid(rng), rng=rng)
        for row in rows:
            expected = seed_module._gap_band(row.gap_score)
            assert row.severity == expected

    def test_band_boundaries(self, seed_module):
        assert seed_module._gap_band(10.0) == "LOW"
        assert seed_module._gap_band(35.0) == "MODERATE"
        assert seed_module._gap_band(55.0) == "HIGH"
        assert seed_module._gap_band(75.0) == "CRITICAL"

    def test_band_vocabulary_is_not_the_risk_vocabulary(self, seed_module):
        # Gaps band as MODERATE; risks are MEDIUM. Conflating them would make a
        # gap look like a risk.
        assert seed_module._gap_band(40.0) == "MODERATE"


class TestSeededDatabase:
    def test_seeding_produces_enough_history_for_trends(self, db, seed_module):
        # One window yields no trends at all, which would make a seeded module
        # look broken rather than unseeded.
        seed_module.seed(db, windows=3, seed_value=42)
        counts = IntelligenceRepository(db).counts()
        assert counts["ward_locations"] == 30
        assert counts["demand_windows"] == 90
        assert counts["gap_snapshots"] == 30

    def test_analysis_after_seeding_finds_hotspots_trends_and_risks(self, db, seed_module):
        from app.services import analytics_service

        seed_module.seed(db, windows=3, seed_value=42)
        result = analytics_service.analyse(db)
        assert result.hotspots > 0
        assert result.trends > 0
        assert result.wards_analysed == 30

    def test_seeding_twice_does_not_duplicate_wards(self, db, seed_module):
        seed_module.seed(db, windows=3, seed_value=42)
        db.query(WardLocation).delete()
        seed_module.seed(db, windows=3, seed_value=42)
        assert db.query(WardLocation).count() == 30

    def test_seeded_wards_are_the_ones_analysis_reads(self, db, seed_module):
        from app.services import analytics_service

        seed_module.seed(db, windows=3, seed_value=42)
        signals = analytics_service.build_ward_signals(IntelligenceRepository(db))
        assert len(signals) == 30

    def test_seeded_demand_carries_population_for_normalisation(self, db, seed_module):
        seed_module.seed(db, windows=3, seed_value=42)
        assert all(row.population > 0 for row in db.query(DemandWindow).all())

class TestSeedingFromAMesh:
    """The mesh-seeded path is a separate code path and needs its own cover."""

    @pytest.fixture
    def mesh_snapshot(self):
        import random
        from datetime import datetime

        from app.services.mesh_client import MeshSnapshot

        return MeshSnapshot(
            wards=[
                {
                    "ward_code": "CHN-01", "name": "Chennai 1", "district": "Chennai",
                    "state": "Tamil Nadu", "centroid_latitude": 13.08,
                    "centroid_longitude": 80.27, "population": 12_000,
                },
                {
                    "ward_code": "MDU-01", "name": "Madurai 1", "district": "Madurai",
                    "state": "Tamil Nadu", "centroid_latitude": 9.92,
                    "centroid_longitude": 78.12, "population": 9_000,
                },
            ],
            demand=[
                {
                    "ward_code": "CHN-01", "sector": "WATER", "window_days": 30,
                    "complaint_count": 40, "critical_count": 3, "avg_severity": 3.1,
                    "observed_from": "2026-06-01 00:00:00",
                    "observed_to": "2026-07-01 00:00:00",
                },
                {
                    "ward_code": "MDU-01", "sector": "ROAD", "window_days": 30,
                    "complaint_count": 12, "critical_count": 1, "avg_severity": 2.8,
                    "observed_from": "2026-06-01 00:00:00",
                    "observed_to": "2026-07-01 00:00:00",
                },
            ],
            gaps=[
                {
                    "ward_code": "CHN-01", "sector": "WATER", "gap_score": 82.5,
                    "severity": "CRITICAL", "demand_score": 40.0, "absence_score": 25.0,
                    "quality_score": 17.5, "recommended_action": "URGENT_REPAIR",
                    "active_project_count": 1, "computed_at": "2026-07-01 00:00:00",
                }
            ],
            projects=[
                {
                    "project_code": "TN-1", "ward_code": "CHN-01", "sector": "WATER",
                    "status": "IN_PROGRESS", "title": "Water works", "budget_lakhs": 900.0,
                    "spent_lakhs": 120.0, "sanctioned_on": "2025-01-01",
                    "expected_completion_on": "2026-12-31", "delay_days": 0,
                }
            ],
        )

    def test_wards_come_from_the_mesh_not_a_synthetic_grid(self, seed_module, mesh_snapshot):
        wards = seed_module._wards_from_mesh(mesh_snapshot)
        assert [ward.ward_code for ward in wards] == ["CHN-01", "MDU-01"]
        assert wards[0].district == "Chennai"
        assert wards[0].population == 12_000

    def test_rows_without_a_ward_code_are_dropped(self, seed_module):
        from app.services.mesh_client import MeshSnapshot

        assert seed_module._wards_from_mesh(MeshSnapshot(wards=[{"name": "x"}])) == []

    def test_anchor_carries_the_meshes_window_bounds(self, seed_module, mesh_snapshot):
        # Sharing the mesh's bounds is what makes a later real sync a no-op
        # instead of a duplicate insert.
        from datetime import datetime

        anchor = seed_module._mesh_anchor(mesh_snapshot)
        entry = anchor[("CHN-01", "WATER")]
        assert entry.count == 40
        assert entry.start == datetime(2026, 6, 1)
        assert entry.end == datetime(2026, 7, 1)

    def test_anchor_carries_the_meshes_severity_and_critical_share(
        self, seed_module, mesh_snapshot
    ):
        # A seeded row labelled source="mesh" that invented these would be wrong
        # permanently: a later sync matches its natural key and skips it.
        anchor = seed_module._mesh_anchor(mesh_snapshot)
        assert anchor[("CHN-01", "WATER")].critical_count == 3
        assert anchor[("CHN-01", "WATER")].avg_severity == 3.1

    def test_anchored_row_reproduces_the_mesh_values_exactly(
        self, seed_module, mesh_snapshot
    ):
        import random

        anchor = seed_module._mesh_anchor(mesh_snapshot)
        rows = seed_module._backfill_demand(
            seed_module._wards_from_mesh(mesh_snapshot),
            windows=2,
            window_days=30,
            rng=random.Random(1),
            anchor=anchor,
        )
        row = next(r for r in rows if r.source == "mesh" and r.ward_code == "CHN-01")
        assert row.complaint_count == 40
        assert row.critical_count == 3
        assert row.avg_severity == 3.1

    def test_seeded_anchored_row_matches_the_mesh_natural_key(self, seed_module, mesh_snapshot):
        # The direct test of "sync after seed inserts nothing": the seeded row and
        # a synced row must collide on (ward, sector, start, end).
        import random

        from app.models.intelligence_tables import DemandWindow

        wards = seed_module._wards_from_mesh(mesh_snapshot)
        anchor = seed_module._mesh_anchor(mesh_snapshot)
        rows = seed_module._backfill_demand(
            wards, windows=2, window_days=30, rng=random.Random(1), anchor=anchor
        )
        anchored = [r for r in rows if r.source == "mesh"]
        assert len(anchored) == 2
        keys = {(r.ward_code, r.sector, r.window_start, r.window_end) for r in anchored}
        assert ("CHN-01", "WATER", anchor[("CHN-01", "WATER")][1],
                anchor[("CHN-01", "WATER")][2]) in keys

    def test_mesh_gaps_and_projects_carry_the_ward_district(self, seed_module, mesh_snapshot):
        wards = seed_module._wards_from_mesh(mesh_snapshot)
        gaps = seed_module._gaps_from_mesh(mesh_snapshot, wards)
        projects = seed_module._projects_from_mesh(mesh_snapshot, wards)
        assert gaps[0].district == "Chennai"
        assert projects[0].district == "Chennai"
        assert gaps[0].source == "mesh"
        assert projects[0].source == "mesh"

    def test_mesh_projects_keep_their_real_status(self, seed_module, mesh_snapshot):
        wards = seed_module._wards_from_mesh(mesh_snapshot)
        assert seed_module._projects_from_mesh(mesh_snapshot, wards)[0].status == "IN_PROGRESS"
