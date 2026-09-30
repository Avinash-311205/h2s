"""Tests for the mesh client.

The client is Module 5's only contact with the upstream, so the behaviour that
matters most is how it behaves when the mesh is absent, partial or freshly
written - not just when everything is present.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app.services.mesh_client import (
    MeshSnapshot,
    fetch_snapshot,
    mesh_available,
    parse_observed_bounds,
    resolve_sqlite_path,
)


@pytest.fixture
def mesh_db(tmp_path):
    """A minimal stand-in for Module 4's database, shaped like the real one."""
    path = tmp_path / "mesh.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE wards (
            ward_code TEXT PRIMARY KEY, name TEXT, district TEXT, state TEXT,
            centroid_latitude REAL, centroid_longitude REAL, population INTEGER
        );
        CREATE TABLE citizen_demand (
            id INTEGER PRIMARY KEY, ward_code TEXT, sector TEXT, category TEXT,
            window_days INTEGER, complaint_count INTEGER, critical_count INTEGER,
            avg_severity REAL, observed_from DATETIME, observed_to DATETIME
        );
        CREATE TABLE gap_records (
            id INTEGER PRIMARY KEY, ward_code TEXT, sector TEXT, gap_score REAL,
            severity TEXT, demand_score REAL, absence_score REAL, quality_score REAL,
            recommended_action TEXT, active_project_count INTEGER, computed_at DATETIME
        );
        CREATE TABLE investment_projects (
            id INTEGER PRIMARY KEY, project_code TEXT, ward_code TEXT, sector TEXT,
            status TEXT, title TEXT, budget_lakhs REAL, spent_lakhs REAL,
            sanctioned_on DATETIME, expected_completion_on DATETIME, delay_days INTEGER
        );
        INSERT INTO wards VALUES
            ('CHN-01', 'Chennai 1', 'Chennai', 'Tamil Nadu', 13.08, 80.27, 12000),
            ('MAD-01', 'Madurai 1', 'Madurai', 'Tamil Nadu', 9.92, 78.12, 9000);
        INSERT INTO citizen_demand VALUES
            (1, 'CHN-01', 'WATER', 'WATER_SUPPLY_DISRUPTION', 30, 40, 4, 3.1,
             '2026-06-01', '2026-07-01');
        INSERT INTO gap_records VALUES
            (1, 'CHN-01', 'WATER', 82.5, 'CRITICAL', 40.0, 25.0, 17.5,
             'URGENT_REPAIR', 1, '2026-07-01');
        INSERT INTO investment_projects VALUES
            (1, 'TN-1', 'CHN-01', 'WATER', 'IN_PROGRESS', 'Water works', 900.0, 120.0,
             '2025-01-01', '2026-12-31', 0);
        """
    )
    connection.commit()
    connection.close()
    return f"sqlite:///{path}"


class TestResolveSqlitePath:
    def test_extracts_the_filesystem_path(self):
        # pathlib normalises the leading "./", which is harmless - what matters is
        # that the path resolves to the same file SQLAlchemy would open.
        assert resolve_sqlite_path("sqlite:///./mesh.db") == Path("mesh.db")

    def test_in_memory_urls_have_no_path(self):
        assert resolve_sqlite_path("sqlite://") is None
        assert resolve_sqlite_path("sqlite:") is None

    def test_non_sqlite_urls_are_declined_rather_than_guessed(self):
        assert resolve_sqlite_path("postgresql://localhost/mesh") is None


class TestMeshAvailable:
    def test_absent_file_is_unavailable(self, tmp_path):
        assert mesh_available(f"sqlite:///{tmp_path}/missing.db") is False

    def test_existing_file_is_available(self, mesh_db):
        assert mesh_available(mesh_db) is True

    def test_in_memory_url_is_never_available(self):
        assert mesh_available("sqlite://") is False


class TestParseObservedBounds:
    def test_explicit_bounds_are_used_as_given(self):
        start, end = parse_observed_bounds(
            {"observed_from": "2026-06-01", "observed_to": "2026-07-01", "window_days": 30}
        )
        assert start == datetime(2026, 6, 1)
        assert end == datetime(2026, 7, 1)

    def test_missing_end_falls_back_to_a_window_before_now(self):
        start, end = parse_observed_bounds({"observed_from": None, "window_days": 30})
        assert (end - start).days == 30

    def test_parsed_bounds_are_naive_utc(self):
        # Must match what SQLite hands back, or de-duplication keys never match.
        start, end = parse_observed_bounds(
            {"observed_from": "2026-06-01T00:00:00+00:00", "observed_to": "2026-07-01T00:00:00Z"}
        )
        assert start.tzinfo is None and end.tzinfo is None

    def test_bounds_are_ordered_oldest_to_newest(self):
        start, end = parse_observed_bounds({"window_days": 90})
        assert start < end


class TestFetchSnapshot:
    def test_reads_every_domain(self, mesh_db):
        snapshot = fetch_snapshot(mesh_db)
        assert snapshot.counts() == {"wards": 2, "demand": 1, "gaps": 1, "projects": 1}
        assert snapshot.is_empty is False

    def test_ward_rows_carry_the_fields_clustering_needs(self, mesh_db):
        ward = fetch_snapshot(mesh_db).wards[0]
        assert ward["ward_code"] == "CHN-01"
        assert ward["centroid_latitude"] == pytest.approx(13.08)
        assert ward["population"] == 12000

    def test_demand_rows_carry_the_window_columns(self, mesh_db):
        demand = fetch_snapshot(mesh_db).demand[0]
        assert demand["ward_code"] == "CHN-01"
        assert demand["complaint_count"] == 40

    def test_district_filter_restricts_wards_only(self, mesh_db):
        snapshot = fetch_snapshot(mesh_db, districts=["Chennai"])
        assert {ward["district"] for ward in snapshot.wards} == {"Chennai"}
        assert len(snapshot.demand) == 1

    def test_unknown_district_yields_no_wards(self, mesh_db):
        assert fetch_snapshot(mesh_db, districts=["Atlantis"]).wards == []

    def test_missing_tables_are_skipped_rather_than_fatal(self, tmp_path):
        # A mesh with only GIS loaded should still produce a partial snapshot.
        path = tmp_path / "partial.db"
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE wards (ward_code TEXT, name TEXT, district TEXT, state TEXT,"
            " centroid_latitude REAL, centroid_longitude REAL, population INTEGER)"
        )
        connection.commit()
        connection.close()
        snapshot = fetch_snapshot(f"sqlite:///{path}")
        assert snapshot.wards == []
        assert snapshot.is_empty is True

    def test_an_empty_database_is_reported_as_empty(self, tmp_path):
        path = tmp_path / "empty.db"
        sqlite3.connect(path).close()
        assert fetch_snapshot(f"sqlite:///{path}").is_empty is True


class TestMeshSnapshot:
    def test_counts_are_zero_for_a_fresh_snapshot(self):
        assert MeshSnapshot().counts() == {"wards": 0, "demand": 0, "gaps": 0, "projects": 0}

    def test_a_snapshot_with_wards_is_not_empty(self):
        assert MeshSnapshot(wards=[{"ward_code": "W1"}]).is_empty is False